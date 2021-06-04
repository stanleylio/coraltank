# This thing would be so much simpler if one could drop the requirement
# of "configuration can be updated on-the-fly without restarting the
# script".
# SL2021
import time, logging, redis, json, shutil, sys, asyncio, sqlite3, random
from datetime import datetime
from common import get_setpoint, get_configuration, trigger_valve, send, get_tank_status
from common import get_probe_offset as _get_probe_offset
sys.path.append('..')
from node.drivers.beep import beep


redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)


def is_valve_control_inhibited():
    return max(0, redis_server.ttl('inhibit')) > 0


async def act(state, second, should_continue_f):
    assert second >= 0
    assert state in ['neutral', 'heating', 'cooling', 'flush']

    if not is_valve_control_inhibited():
        trigger_valve(state)
        redis_server.set('tank_state', json.dumps(state), ex=max(2*second, 1))

    for _ in range(int(second)):
        if not should_continue_f():
            logging.debug('early break')
            break
        await asyncio.sleep(1)


def get_probe_offset(*, force_refresh=False):
    try:
        tmp = redis_server.get('c0')
        if tmp is not None and not force_refresh:
            return json.loads(tmp)
        else:
            tmp = _get_probe_offset()
            redis_server.set('c0', json.dumps(tmp))
            return tmp
    except:
        logging.exception('failed to calculate probe offset. default to 0')
        return 0


def get_corrected_current_temp():
    tmp = float('nan')
    try:
        tmp = redis_server.get('t0')
        if tmp is not None:
            tmp = json.loads(tmp) + get_probe_offset(force_refresh=random.random() > 0.999)
            return round(tmp, 6)
    except:
        logging.debug('wut?')
    return tmp


async def task_deployed():
    # Execeute the experiment temperature profile

    tank_state = 'neutral'

    def wubalubadubdub():
        return should_continue and ('deployed' == get_tank_status())

    while should_continue:
        thermostat_loop_period_second = int(get_configuration('thermostat_loop_period_second'))
        if not wubalubadubdub():
            logging.debug('(task_deployed sleeping)')
            await asyncio.sleep(3*random.random())
            continue

        deadband = float(get_configuration('deadband_celsius'))
        high_alarm = float(get_configuration('high_alarm_celsius'))
        low_alarm = float(get_configuration('low_alarm_celsius'))
        setpoint = get_setpoint(force_read=False)

        redis_server.set('setpoint', json.dumps(setpoint), ex=2*thermostat_loop_period_second)

        current_temp = get_corrected_current_temp()

        # determinate desired tank_state
        
        if setpoint is None or setpoint != setpoint:
            logging.warning('No current setpoint, or the "setpoint" is NA')
            tank_state = 'neutral'
        elif current_temp is None or current_temp != current_temp:
            logging.warning('No current temperature reading')
            tank_state = 'neutral'
        elif current_temp > high_alarm or current_temp < low_alarm:
            logging.warning(f"Out of bound: {current_temp} not within [{low_alarm},{high_alarm}]")
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

        logging.info(f"Deployed: current={current_temp} \u00b0C, setpoint={setpoint} \u00b0C, tank_state={tank_state}")

        # apply desired tank_state
        await act(tank_state, thermostat_loop_period_second, wubalubadubdub)


async def task_maintenance():
    # Run a maintenance regime

    def wubalubadubdub():
        return should_continue and ('maintenance' == get_tank_status())
    
    while should_continue:
        if not wubalubadubdub():
            logging.debug('(task_maintenance sleeping)')
            await asyncio.sleep(3*random.random())
            continue

        # "the concept of setpoint does not make sense in this context"
        #redis_server.set('setpoint', float('nan'))
        redis_server.delete('setpoint')

        logging.info('Maintenance: heating')
        await act('heating', 5, wubalubadubdub)
        if not wubalubadubdub():
            continue
        logging.info('Maintenance: cooling')
        await act('cooling', 5, wubalubadubdub)
        if not wubalubadubdub():
            continue
        logging.info('Maintenance: flush')
        for _ in range(50):
            if not wubalubadubdub():
                break
            await act('flush', 1, wubalubadubdub)
        logging.info('Maintenance: neutral')
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


async def task_relay():
    # send states to the cloud

    VALVE_STATE_M = {'neutral':0, 'heating':1, 'cooling':2, 'flush':3}
    def f(v):
        try:
            return json.loads(redis_server.get(v))
        except:
            return None

    while should_continue:
        try:
            cloud_report_interval_second = int(get_configuration('cloud_report_interval_second'))
            senderid = get_configuration('serial_number')
            tank_number = int(get_configuration('tank_number'))
            uptime_second = int(float(open('/proc/uptime').readline().split()[0]))
            free = int(shutil.disk_usage('/').free/1e6)
            cpu_temp = round(float(open('/sys/class/thermal/thermal_zone0/temp').readline())*1e-3, 1)
            tank_state = f('tank_state')
            d = {'ts':round(time.time(), 3),
                 't0':get_corrected_current_temp(),
                 'c0':get_probe_offset(),
                 's0':f('setpoint'),
                 'v0':VALVE_STATE_M.get(tank_state, None),
                 'k':tank_number,
                 'uptime_second':uptime_second,
                 'freeMB':free,
                 'cpu_temp':cpu_temp,
                 }
            #print(d)
            redis_server.set('uptime_second', json.dumps(uptime_second), ex=2*cloud_report_interval_second)
            redis_server.set('freeMB', json.dumps(free), ex=2*cloud_report_interval_second)
            redis_server.set('cpu_temp', json.dumps(cpu_temp), ex=2*cloud_report_interval_second)
            send(d, senderid)
            
            for _ in range(cloud_report_interval_second):
                await asyncio.sleep(1)
                if not should_continue:
                    break
        
        except:
            # low priority task, so catch-all and ignore.
            logging.exception('wut')
            await asyncio.sleep(10)


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
            logging.warning(f"valve operation inhibited")
            warning = True
        else:
            warning = False
        
        if warning:
            beep(on=1, off=0)
            for _ in range(9):
                await asyncio.sleep(1)
        else:
            await asyncio.sleep(1)
            continue


async def task_temperature_log():

    log_period_second = 60

    def f(v):
        try:
            return json.loads(redis_server.get(v))
        except:
            return redis_server.get(v)

    while should_continue:
        await asyncio.sleep(log_period_second)
        
        keep_day = int(get_configuration('keep_local_temperature_record_days'))
        
        if keep_day > 0:
            now = int(time.time())
            nowdt = str(datetime.now())[:19]
            try:
                t0 = f('t0')
                setpoint = f('setpoint')

                conn = sqlite3.connect('/var/www/html/records.db')
                if random.random() > 0.9999:
                    conn.execute('VACUUM')
                with conn as conn:
                    cur = conn.cursor()
                    cur.execute(f"""CREATE TABLE IF NOT EXISTS tank_temperature (
                                    'ts' INTEGER PRIMARY KEY,
                                    'dt' TEXT NOT NULL,
                                    't0' REAL,
                                    'setpoint' REAL
                                    )""")
                    if random.random() > 0.99:
                        cur.execute(f"""DELETE FROM tank_temperature WHERE ts < ?""", (now - keep_day*24*3600, ))
                    cur.execute(f"""INSERT OR IGNORE INTO tank_temperature ('ts', 'dt', 't0', 'setpoint') VALUES (?,?,?,?)""", (now, nowdt, t0, setpoint, ))
            except:
                # low priority task, so catch-all and ignore.
                logging.exception('?')


if '__main__' == __name__:
    
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('pika').setLevel(logging.ERROR)
    logging.getLogger('common').setLevel(logging.WARNING)

    # let everything else settles down first
    for i in range(3, 0, -1):
        print(i)
        time.sleep(1)

    get_setpoint(force_read=True)   # force a profile.csv -> profile.db conversion

    with sqlite3.connect('/var/www/html/records.db') as conn:
        cur = conn.cursor()
        cur.execute(f"""CREATE TABLE IF NOT EXISTS operation (
                        'ts' REAL NOT NULL,
                        'dt' TEXT NOT NULL,
                        'e' TEXT NOT NULL,
                        'm' TEXT NOT NULL
                        )""")

        now = int(time.time())
        nowdt = str(datetime.now())[:19]
        event = 'start'
        message = json.dumps({'tank_number':get_configuration('tank_number'),
                              'controller_name':get_configuration('controller_name'),
                              }, separators=(',', ':'))
        cur.execute(f"""INSERT OR IGNORE INTO operation ('ts', 'dt', 'e', 'm') VALUES (?,?,?,?)""",
                    (now, nowdt, event, message, ))

    should_continue = True

    # "when it beeps, we have a reasonable belief that the init process
    # succeeded"
    beep(on=2, off=0)

    try:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(
            task_deployed(),
            task_maintenance(),
            task_relay(),
            task_warning(),
            task_temperature_log(),
            ))
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')
    finally:
        # always turn off all valves on exit
        trigger_valve('neutral')
