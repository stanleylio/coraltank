# Want to create more work and failure modes? Stick a Kalman filter or
# two in here... TODO
# SL2021
import time, logging, sys, statistics, redis, asyncio, json, glob, re, random, typing
from datetime import datetime
from os.path import join, expanduser, basename
sys.path.append(expanduser('~'))
sys.path.append('..')
from node.drivers.tsys01 import TSYS01
from scipy.signal import medfilt
import numpy as np
from common import get_configuration, get_probe_offset


logger = logging.getLogger(__name__)


def read_ds18b20_serial_numbers():
    r = [basename(x) for x in glob.glob('/sys/bus/w1/devices/28-*')]
    return sorted([rr[3:] for rr in r if rr.startswith('28-')])


# The resolution of the DS18B20 is only 0.0625 °C, and read time is long
# (~750 ms). You'd have to keep a long history if you want to
# meaningfully oversample them.
def read_ds18b20s():
    #return [int(re.match('.+t=(?P<t>\d{5})$', open(join(x, 'w1_slave')).read(), re.MULTILINE | re.DOTALL).group('t'))*1e-3 for x in sorted(glob.glob('/sys/bus/w1/devices/28-*'))]
    SN = read_ds18b20_serial_numbers()
    T = [int(re.match('.+t=(?P<t>\d{5})$', open(join(x, 'w1_slave')).read(), re.MULTILINE | re.DOTALL).group('t'))*1e-3 for x in [f"/sys/bus/w1/devices/28-{sn}" for sn in SN]]
    return dict(zip(SN, T))


def read_tsys01():
    """Take a bunch of measurements from the sensor, reject 3-σ
    outliers, and return the sample average of what remains.

    Design note + rant:
    It only reads one probe, but (if successful) it returns a dict of
    serial number : reading to follow read_ds18b20s()'s output format.

    All this complexity (plus the TSYS01 driver rewrite) in order to
    record the DS18B20 probed(s) individually (as opposed to, you know,
    treating multiple of them as one unit with better precision).
    
    Operationally, it's not like you can identify which DS18B20 is which
    since there is no label or marking on the probes, and thus no
    ordering (e.g. you can't designate them as "probe1", "probe2",
    "probe3" in the control code and the database). But if you had to
    pull them apart one by one to identify and test them anyway, you may
    as well test their accuracy there and then instead. But that's the
    requirement and so that's how it is done here.
    """
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
        return {f"{sensor.SN:03x}":round(statistics.mean(R), 4), }  # TSYS01 SN is 24-bit
    except:
        logger.exception('no tsys')
        return None
    

def get_temperature() -> typing.Tuple[float, dict]:
    try:
        T = read_ds18b20s()
        return statistics.median(T.values()), T
    except statistics.StatisticsError:
        logger.warning('Fail to read DS18B20. Fall back to TSYS01')

    T = read_tsys01()
    if T is None:
        return float('nan'), {}
    else:
        return statistics.median(T.values()), T


async def task_sample():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    refresh_period_second = 5 + int(random.random())

    while should_continue:
        t0,probes = get_temperature()
        redis_server.set('t0',
                         json.dumps(t0),
                         ex=2*refresh_period_second)
        redis_server.set('t_probes',
                         json.dumps(probes),
                         ex=2*refresh_period_second)

        c0 = get_probe_offset()
        redis_server.set('c0',
                         json.dumps(c0),
                         ex=2*refresh_period_second)

        t0c = t0 + c0
        redis_server.set('t0c',
                         json.dumps(t0c),
                         ex=2*refresh_period_second)

        logger.info(f"{str(datetime.now())[:19]}, t0={t0:.3f}°C, c0={c0:.6f}°C, t0c={t0c:.3f}°C")

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

