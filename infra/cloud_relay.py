import sys, logging, time, redis, asyncio, shutil, json
sys.path.append('..')
from common import get_configuration, send_to_meshlab, send_to_the_one_true_master


logger = logging.getLogger(__name__)


async def task_relay():
    # send state to the cloud

    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    VALVE_STATE_M = {'neutral':0, 'heating':1, 'cooling':2, 'flush':3, }
    def f(v):
        try:
            return json.loads(redis_server.get(v))
        except TypeError:
            return None

    while should_continue:
        cloud_report_interval_second = int(get_configuration('cloud_report_interval_second'))
        
        tank_number = int(get_configuration('tank_number'))
        uptime_second = int(float(open('/proc/uptime').readline().split()[0]))
        free = int(shutil.disk_usage('/').free/1e6)
        cpu_temp = round(float(open('/sys/class/thermal/thermal_zone0/temp').readline())*1e-3, 1)
        tank_state = f('tank_state')
        d = {'ts':time.time(),
             't0':f('t0'),
             't0c':f('t0c'),
             'c0':f('c0'),
             's0':f('setpoint'),
             'v0':VALVE_STATE_M.get(tank_state, None),
             'pwm_hot':f('pwm_hot'),
             'pwm_cold':f('pwm_cold'),
             'pwm_ambient':f('pwm_ambient'),
             'k':tank_number,
             'uptime_second':uptime_second,
             'freeMB':free,
             'cpu_temp':cpu_temp,
             }
        logging.info(d)
        redis_server.set('uptime_second', json.dumps(uptime_second), ex=2*cloud_report_interval_second)
        redis_server.set('freeMB', json.dumps(free), ex=2*cloud_report_interval_second)
        redis_server.set('cpu_temp', json.dumps(cpu_temp), ex=2*cloud_report_interval_second)

        send_to_meshlab(d)

        try:
            topic_suffix = f"op"
            send_to_the_one_true_master(topic_suffix, d)
        except ConnectionRefusedError:
            logger.warning('ouch')
        
        await asyncio.sleep(cloud_report_interval_second)


if '__main__' == __name__:
    
    logging.basicConfig(level=logging.DEBUG)
    logging.getLogger('pika').setLevel(logging.WARNING)
    logging.getLogger('common').setLevel(logging.WARNING)

    should_continue = True

    async def main():
        await asyncio.gather(
            task_relay(),
            )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')
