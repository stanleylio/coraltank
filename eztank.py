"""This thing would be so much simpler if one could drop the requirement
of "configuration can be updated on-the-fly without restarting the
script".

There are all kinds of fun things you could do if you have the budget
and can tolerate short term pain. For example, instead of "setpoints"
that come in one at a time, we have access to the full past history and
future profile. The controller can look ahead instead of considering
only the closest setpoint. Short of that, you can also interpolate
between setpoints in the profile. But then that "NA" flag requirement
gets in the way so that complicates things a lot. Another case of
"nice-to-haves" getting in the way of important features.

Another cool thing you get "for free" from interpolation, other than a
smoother profile curve, is that the user could just specify key points
(peaks, troughs, inflection points...) in the profile instead of a
densely sampled time series.

SL2021
"""
import time, logging, redis, json, sys, asyncio, random
from datetime import datetime
from common import get_setpoint, get_configuration, set_valve_pwm, trigger_valve_direct, get_tank_status, is_valve_control_inhibited, add_operation_entry
from PID import PID
sys.path.append('..')
from node.drivers.beep import beep


logger = logging.getLogger(__name__)
redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)


async def act(state, second, should_continue_f, *, p=1.0):
    assert second >= 0 and type(second) is int
    assert state in {'neutral', 'heating', 'cooling', 'flush', }
    assert p >= 0 and p <= 1

    if not is_valve_control_inhibited():
        '''if 'deployed' == get_tank_status():
            set_valve_pwm(state, p=p)
        elif 'maintenance' == get_tank_status():
            # The PWM controller period is long, so if you rely on
            # setting the PWM and wait, you might miss short events like
            # maintenance valve actions. Either PWM controller has to be
            # aware of a new type of "act NOW" events, or you have to
            # talk to the valve server directly and bypass the PWM
            # controller.
            trigger_valve_direct(state)
        else:
            pass'''
        trigger_valve_direct(state)     # sigh.
        
        redis_server.set('tank_state', json.dumps(state), ex=max(2*second, 1))
    else:
        logger.info('(valve control inhibited, no op)')

    for _ in range(int(second)):
        if not should_continue_f():
            logger.debug('early break')
            break
        await asyncio.sleep(1)


def get_corrected_current_temp():
    try:
        return round(json.loads(redis_server.get('t0c')), 6)
    except TypeError:
        logger.exception('a dog and a mug in a room on fire.jpg')
    return float('nan')


async def task_deployed():
    # Execute the experiment temperature profile

    tank_state = 'neutral'

    def wubalubadubdub():
        return should_continue and ('deployed' == get_tank_status())

    while should_continue:
        thermostat_loop_period_second = int(get_configuration('thermostat_loop_period_second'))
        if not wubalubadubdub():
            #logger.debug('(task_deployed sleeping)')
            await asyncio.sleep(3*random.random())
            continue

        deadband = float(get_configuration('deadband_celsius'))
        high_alarm = float(get_configuration('high_alarm_celsius'))
        low_alarm = float(get_configuration('low_alarm_celsius'))
        setpoint = get_setpoint(force_read=False)   # refreshed when new upload occurs (see the web app)
        redis_server.set('setpoint', json.dumps(setpoint), ex=2*thermostat_loop_period_second)
        current_temp = get_corrected_current_temp()

        pwm = 1

        if setpoint is None or setpoint != setpoint:
            logger.warning('No current setpoint, or the "setpoint" is NA')
            tank_state = 'neutral'
        elif current_temp is None or current_temp != current_temp:  # NaN != NaN
            logger.warning('No current temperature reading')
            tank_state = 'neutral'
        elif current_temp > high_alarm or current_temp < low_alarm:
            logger.warning(f"{current_temp} outside [{low_alarm},{high_alarm}]")
            tank_state = 'flush'
        elif current_temp >= setpoint + deadband/2:
            tank_state = 'cooling'
        elif current_temp <= setpoint - deadband/2:
            tank_state = 'heating'
        elif current_temp < setpoint + deadband/2 and current_temp > setpoint:
            if 'heating' == tank_state:
                tank_state = 'neutral'
        elif current_temp > setpoint - deadband/2 and current_temp <= setpoint:
            if 'cooling' == tank_state:
                tank_state = 'neutral'

        logger.info(f"Deployed: SP={setpoint:.2f}°C, PV={current_temp:.2f}°C, e={setpoint - current_temp:+.3f}°C; {tank_state} ({100*pwm:.0f}%)")

        # apply desired tank_state
        await act(tank_state, thermostat_loop_period_second, wubalubadubdub, p=pwm)
        await asyncio.sleep(0.1*random.random())


async def task_maintenance():
    # Run a maintenance regime

    def wubalubadubdub():
        return should_continue and ('maintenance' == get_tank_status())
    
    while should_continue:
        if not wubalubadubdub():
            #logger.debug('(task_maintenance sleeping)')
            await asyncio.sleep(3*random.random())
            continue

        # "The concept of setpoint does not make sense in this context".
        # The proportional valve controller cedes control of the valves
        # if the variables are undefined.
        for k in {'setpoint', 'pwm_hot', 'pwm_cold', 'pwm_ambient', }:
            redis_server.delete(k)

        logger.info('Maintenance: heating')
        await act('heating', 5, wubalubadubdub)
        if not wubalubadubdub():
            continue
        logger.info('Maintenance: cooling')
        await act('cooling', 5, wubalubadubdub)
        if not wubalubadubdub():
            continue
        logger.info('Maintenance: flush')
        for _ in range(50):
            if not wubalubadubdub():
                break
            await act('flush', 1, wubalubadubdub)
        logger.info('Maintenance: neutral')
        for _ in range(int(get_configuration('maintenance_cycle_interval_second'))):
            if not wubalubadubdub():
                break
            await act('neutral', 1, wubalubadubdub)
        # "Why", you ask.
        # Imagine this sequence of events:
        # 1. Controller is in maintenance mode, but it's in between
        #    valve toggles (50~3600 seconds wait, i.e. more than I have
        #    patience for)
        # 2. Operator paused the controller (valve operations inhibited)
        # 3. Operator turned on all valves
        # 4. Operator relinquish control and left
        # 5. Controller resumes the long waiting phase, oblivious of the
        #    fact that all valves are still open (not good, in case
        #    that's not obvious).
        # 
        # In general, if you can't guarantee that the state of a system
        # will persist until and unless changed by you alone (i.e. the
        # state won't change without your input, and no one else could
        # change it), you either have to repeatedly apply your desired
        # state (operation must be idempotent in this case), or you have
        # to detect external changes and revert them when required. The
        # above takes the former approach.


async def task_warning():
    """When one of these happens
            1. tank_status in the configuration file is invalid, or
            2. valve control has been inhibited by operator
            ... 3. ... I guess you could add a "temperature out of range" condition too...
        Do these:
            1. Don't touch the valves
            2. Make noise
    """
    while should_continue:
        warning = True
        if is_valve_control_inhibited():
            logger.warning('valve operation inhibited')
            warning = True
        else:
            warning = False
        
        if warning:
            beep(on=1, off=0)
            await asyncio.sleep(9)
        else:
            await asyncio.sleep(1)
            continue


if '__main__' == __name__:
    
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('pika').setLevel(logging.WARNING)
    logging.getLogger('common').setLevel(logging.WARNING)

    # Let everything else settle down first
    # Add little bit of randomness
    # Why? Power outage -> power restored -> all tanks try to open valves at
    # the same time -> power spike + pressure dip
    for i in range(int(3*random.random()) + 1, 0, -1):
        print(i)
        time.sleep(1)

    get_setpoint(force_read=True)   # force a profile.csv -> profile.db conversion

    message = json.dumps({'tank_number':get_configuration('tank_number'),
                          'controller_name':get_configuration('controller_name'),
                          }, separators=(',', ':'))
    add_operation_entry('start', message)

    should_continue = True

    # "when it beeps, we have a reasonable belief that the init process
    # succeeded"
    beep(on=2, off=0)

    async def main():
        await asyncio.gather(
            task_deployed(),
            task_maintenance(),
            task_warning(),
            )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')
    finally:
        # always turn off all valves on exit
        set_valve_pwm('neutral')
        trigger_valve_direct('neutral')
