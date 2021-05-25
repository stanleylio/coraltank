#
# SL2021
import time, logging, sys, statistics, redis, asyncio, json
from datetime import datetime
from os.path import expanduser
sys.path.append(expanduser('~'))
sys.path.append('..')
from node.drivers.tsys01 import TSYS01
from scipy.signal import medfilt
import numpy as np
from common import get_configuration


def get_temperature():
    """Take a bunch of measurements from temperature sensor, reject
    3-sigma outliers, and return the sample average."""
    try:
        sensor = TSYS01()
        R = []
        for i in range(13):
            try:
                R.append(sensor.read())
                time.sleep(0.001)
            except:
                pass

        #return round(float(np.mean(medfilt(R, 5))), 4)
        m,std = statistics.mean(R), statistics.stdev(R)
        R = [r for r in R if abs(r - m) <= 3*std]
        return round(statistics.mean(R), 4)
    except:
        logging.exception('{},channel={}'.format(time.time(), channel))
        return None


def raw_temperature():
    try:
        return round(TSYS01().read(), 4)
    except:
        logging.exception('{},channel={}'.format(time.time(), channel))
        return None


async def task_sample():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    refresh_period_second = 6

    while should_continue:
        t = get_temperature()
        logging.info(f'{str(datetime.now())[:19]}, {t:.3f} \u00b0C')
        redis_server.set('t0',
                         json.dumps(t),
                         ex=2*refresh_period_second)

        await asyncio.sleep(refresh_period_second)


if '__main__' == __name__:

    logging.basicConfig(level=logging.DEBUG)

    should_continue = True

    try:
        asyncio.get_event_loop().run_until_complete(asyncio.gather(
            task_sample(),
            ))
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')

