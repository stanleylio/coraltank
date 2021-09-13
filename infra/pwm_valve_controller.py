"""This provides proportional control of the valves by adjusting the
duty cycle (PWM, but slow). Each thermostat_loop_period_second period is
divided into an ON phase and an OFF phase.

Control is over redis through these variables:

    pwm_hot
    pwm_cold
    pwm_ambient
    
They are numbers from 0 to 1 denoting the power output from 0% to 100%
for the three valves.

If the variable is undefined, this task does nothing (it does not set
them to 0!). If the variable is out of bound, it clips it to the [0,1]
interval.

This script is aware of the "inhibit" operation (disable automatic valve
control).

... hum I wonder. How about one PID controller per valve? That
seamlessly takes care of
    1. Proportional mixing (hot > hot+ambient > ambient > ambient+cold >
    cold)
    2. Different feeds require different parameters (e.g. easier to heat
    up than cool down -> cold feed P ld be more aggressive than hot feed
    P), and
    3. Truly one func to rule them all (see the assert)

"""
import sys, logging, asyncio, redis, json, random
from xmlrpc.client import ServerProxy
sys.path.append('..')
from common import get_configuration, is_valve_control_inhibited


logger = logging.getLogger(__name__)


# One func to handle any number of valves. Added a new valve? Just spawn
# another instance of this.
async def task_valve_tender(valve):

    assert valve in {'hot', 'cold', 'ambient', }

    proxy = ServerProxy('http://localhost:8001/')
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    await asyncio.sleep(2.3*random.random())

    try:
        while should_continue:
            thermostat_loop_period_second = int(get_configuration('thermostat_loop_period_second'))
            assert thermostat_loop_period_second > 0
            pwm_min_actuation_second = float(get_configuration('pwm_min_actuation_second'))
            assert pwm_min_actuation_second >= 0

            pwm = None

            try:
                pwm = json.loads(redis_server.get(f"pwm_{valve}"))
            except TypeError:
                logger.warning(f"pwm_{valve} undefined. No action.")
                pwm = None

            if pwm is not None:
                if pwm < 0 or pwm > 1:
                    logger.warning(f"pwm_{valve}={pwm} out of bound: {pwm}; clipped")
                pwm = min(1, max(0, pwm))
                
                logger.info(f"{valve}: {100*pwm:.0f}%")

                # ... I guess you could randomize the order of these two too.

                # "if the resulting valve actuation time (ON or OFF) in
                # seconds is less than this, don't bother"
                on_time = pwm*thermostat_loop_period_second
                off_time = thermostat_loop_period_second - on_time
                
                if not is_valve_control_inhibited():
                    if on_time >= pwm_min_actuation_second:
                        proxy.valve_on(valve)
                        await asyncio.sleep(on_time)
                else:
                    await asyncio.sleep(1)

                # The inhibit state could have changed during that wait.
                # Better check again. Nothing replaces an atomic op of
                # course, but failure is rare and is a mere annoyance.
                if not is_valve_control_inhibited():
                    if off_time >= pwm_min_actuation_second:
                        proxy.valve_off(valve)
                        await asyncio.sleep(off_time)
                else:
                    await asyncio.sleep(1)
            else:
                logger.info(f"pwm_{valve} undefined. No action.")
                await asyncio.sleep(thermostat_loop_period_second)

            # so the valves can eventually go out of sync. (to avoid
            # power spikes and pressure dips)
            await asyncio.sleep(0.001*random.random())

    finally:
        # always turn off all valves on exit
        proxy.valve_off(valve)
        # but should you also remove the keys from redis?
        # hum... questionable.
        #if redis_server.exists(f"pwm_{valve}"):
        #    redis_server.delete(f"pwm_{valve}")


if '__main__' == __name__:
    
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('pika').setLevel(logging.ERROR)
    logging.getLogger('common').setLevel(logging.WARNING)

    should_continue = True

    # the newer asyncio.run() is better than the old
    # run_until_complete(). run() actually lets the tasks do their
    # cleanup.
    async def main():
        await asyncio.gather(
            task_valve_tender('hot'),
            task_valve_tender('cold'),
            task_valve_tender('ambient'),
            )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')
