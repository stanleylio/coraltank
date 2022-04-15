import logging, configparser, time, sys, pika, csv, sqlite3, os, json, redis, socket
from datetime import datetime, timedelta
from xmlrpc.client import ServerProxy
sys.path.append('/home/pi')
from cred import cred
from node.z import send as formatsend
from node.helper import init_rabbit, dt2ts
import numpy as np


logger = logging.getLogger(__name__)


# hindsight: configuration changes over time, so really the
# configuration parameters are time series, and should have been
# snapshotted into a database.
def get_configuration(key, *, default=None):
    config = configparser.ConfigParser()
    config.read('/var/www/html/config/config.txt')
    for section in config:
        try:
            return config[section][key]
        except KeyError:
            pass
    return default


def get_probe_offset():
    calibration_sample_size = max(0, int(get_configuration('calibration_sample_size', default=0)))
    if calibration_sample_size <= 0:
        logger.info('calibration_sample_size <= 0, invalid, or undefined; default to 0')
        return 0
    
    with sqlite3.connect('/var/www/html/records.db') as conn:
        cur = conn.cursor()
        try:
            # requirement: t0 is before-correction!
            cur.execute("""SELECT AVG(haha) FROM
                            (SELECT tref - t0 AS haha
                            FROM userlog
                            WHERE t0 is not NULL
                            AND tref is not NULL
                            ORDER BY ts DESC
                            LIMIT ?)""", (calibration_sample_size, ))
            tmp = cur.fetchone()
            if tmp is not None:
                return tmp[0]
            logger.warning('no probe cal data. default to 0')
        except sqlite3.OperationalError:
            logger.warning('probably a missing userlog table (i.e. no user log and thus no cal data yet. default to 0)')
        return 0


def add_operation_entry(event, message):
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
        cur.execute(f"""INSERT OR IGNORE INTO operation ('ts','dt','e','m') VALUES (?,?,?,?)""",
                    (now, nowdt, event, message, ))


def set_tank_status(status):
    assert status in {'deployed', 'maintenance', }
    add_operation_entry('tank_status_change', status)


def get_tank_status():
    with sqlite3.connect('/var/www/html/records.db') as conn:
        cur = conn.cursor()
        cur.execute("""SELECT m FROM operation
                        WHERE `e`=='tank_status_change'
                        ORDER BY `rowid` DESC LIMIT 1""")
        tmp = cur.fetchone()
        if tmp is None:
            logger.warning('query fell through w/ no result. default to maintenance mode (new Pi?).')
            return 'maintenance'
        else:
            tmp = tmp[0]
            assert tmp in {'deployed', 'maintenance'}, f"{tmp} is not a valid tank_status"
            return tmp


def set_valve_pwm(tank_state, *, p=1.0, ex=3600):
    assert p is None or (p >= 0 and p <= 1)
    assert type(ex) is int
    
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    if p is not None:
        if tank_state == 'neutral':
            redis_server.set(f"pwm_hot", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_cold", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_ambient", json.dumps(0), ex=ex)
        elif tank_state == 'heating':
            redis_server.set(f"pwm_hot", json.dumps(p), ex=ex)
            redis_server.set(f"pwm_cold", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_ambient", json.dumps(0), ex=ex)
        elif tank_state == 'cooling':
            redis_server.set(f"pwm_hot", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_cold", json.dumps(p), ex=ex)
            redis_server.set(f"pwm_ambient", json.dumps(0), ex=ex)
        elif tank_state == 'flush':
            redis_server.set(f"pwm_hot", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_cold", json.dumps(0), ex=ex)
            redis_server.set(f"pwm_ambient", json.dumps(p), ex=ex)
        else:
            logger.error(f"unknown tank_state {tank_state}")
    else:
        # This disables the pwm valve controller.
        # "tank_state" is ignored.
        for k in {'pwm_hot', 'pwm_cold', 'pwm_ambient', }:
            redis_server.delete(k)


def trigger_valve_direct(tank_state):
    # You either have to suppress the PWM controller, update it to be
    # cooperative, or coax it to do what you want and pray/wait that it
    # does.
    #
    # There is no way around it: if you want the PWM controller to be
    # responsive, it has to be updated to be cooperative. Switching to
    # transaction-like interface like the valve server is one option,
    # but I like the "free" observability of redis.
    #
    # Redis has some kind of pub-sub "on-change" mechanism too, but I
    # want to keep it as a simple K-V store.
    try:
        set_valve_pwm(tank_state, p=None)
    except:
        logger.warning('?')
    
    proxy = ServerProxy('http://localhost:8001/')
    
    if tank_state == 'neutral':
        proxy.valve_off('hot')
        proxy.valve_off('cold')
        proxy.valve_off('ambient')
    elif tank_state == 'heating':
        proxy.valve_on('hot')
        proxy.valve_off('cold')
        proxy.valve_off('ambient')
    elif tank_state == 'cooling':
        proxy.valve_off('hot')
        proxy.valve_on('cold')
        proxy.valve_off('ambient')
    elif tank_state == 'flush':
        proxy.valve_off('hot')
        proxy.valve_off('cold')
        proxy.valve_on('ambient')
    else:
        logger.error(f"unknown tank_state {tank_state}")


def is_valve_control_inhibited():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
    # if the key does not exist, ttl returns -2. no exception raised
    # there.
    #return max(0, redis_server.ttl('inhibit')) > 0
    return redis_server.ttl('inhibit') > 0


def send_to_meshlab(d):
    senderid = get_configuration('serial_number')
    exchange = 'uhcm'
    connection, channel = init_rabbit('pi', cred['rabbitmq'])
    channel.exchange_declare(exchange=exchange, exchange_type='topic', durable=True)
    channel.basic_publish(exchange=exchange,
                          routing_key=senderid + '.s',
                          body=formatsend(None, d, src=senderid).strip(),
                          properties=pika.BasicProperties(delivery_mode=2,
                                                          content_type='text/plain',
                                                          expiration=str(3*24*3600*1000)))


def send_to_one_true_master(message_type, d):
    senderid = get_configuration('controller_name')
    exchange = 'gbrf'
    routing_key = f"{message_type}.{senderid}.s"
    connection, channel = init_rabbit('pi',
                                      cred['rabbitmq'],
                                      exchange=exchange,
                                      host='localhost',
                                      )
    channel.exchange_declare(exchange=exchange, exchange_type='topic', durable=True)
    channel.basic_publish(exchange=exchange,
                          routing_key=routing_key,
                          body=json.dumps(d, separators=(',', ':')).encode('utf-8'),
                          properties=pika.BasicProperties(delivery_mode=2,
                                                          content_type='text/plain',
                                                          expiration=str(3*24*3600*1000)))


def get_setpoint(*, fn='/var/www/html/config/profile.csv', force_read=True):
    """HST. The time zone is HST. You can stop reading now.

    From the temperature profile, locate the temperature point with a
    timestamp closest to "now". If "now" is not covered by the profile,
    then return NaN.
    
    More ideas: If you want to be fancy you can include hard-limits on
    the profile in the config.txt.

    Instead of sticking to the closest setpoint, how about some
    interpolation with the nearest setpoint(s)?
    
    Generator... hum...
    """

    now = time.time()

    # * * * * *
    # Timestamps in the temperature profile are interpreted as in HST!
    # Under the hood everything in the controller uses UTC.
    # * * * * *
    timezoneoffset = timedelta(hours=-10)

    dbfn = fn.rsplit('.', 1)[0] + '.db'
    should_read = force_read or not os.path.exists(dbfn)

    with sqlite3.connect(dbfn) as conn:
        cur = conn.cursor()
        
        if should_read:
            cur.execute(f"""DROP TABLE IF EXISTS A""")
            cur.execute(f"""CREATE TABLE IF NOT EXISTS A (
                            'ts' INTEGER PRIMARY KEY,
                            't' REAL NOT NULL
                            )""")
            conn.execute('VACUUM')
            with open(fn, newline='') as csvfile:
                for tmp in csv.reader(csvfile):
                    try:
                        x,y = tmp
                        if 12 == len(x):
                            x = int(dt2ts(datetime.strptime(x, '%Y%m%d%H%M') - timezoneoffset))
                        elif 14 == len(x):
                            x = int(dt2ts(datetime.strptime(x, '%Y%m%d%H%M%S') - timezoneoffset))
                        else:
                            raise ValueError(f"Bad timestamp: {x}")
                        y = float(y) if y != 'NA' else 'NA'
                        cur.execute(f"""INSERT OR REPLACE INTO A ('ts', 't') VALUES (?,?)""", (x, y, ))
                    except ValueError:
                        logger.error(f'invalid line in CSV: {tmp}')
                conn.commit()

        cur.execute(f"""SELECT MAX(ts),t FROM A
                        WHERE ts <= ?""", (now, ))
        leftt,leftv = cur.fetchone()
        if leftt is None:
            # the profile does not cover this moment in time (we're too
            # early, or the profile is empty)
            logger.warning('>we are here< [profile]')
        if 'NA' == leftv:
            leftv = float('nan')
            
        cur.execute(f"""SELECT MIN(ts),t FROM A
                        WHERE ts >= ?""", (now, ))
        rightt,rightv = cur.fetchone()
        if rightt is None:
            # the profile does not cover this moment in time (we're too
            # late, or the profile is empty)
            logger.warning('[profile] >we are here<')
        if 'NA' == rightv:
            rightv = float('nan')

        logger.debug(f'{leftt, leftv, rightt, rightv}')
        # "return NaN if we are outside of the window the profile covers"
        if leftt is None or rightt is None:
            return float('NaN')

        # so both my left and right are valid numbers.
        return leftv if now - leftt <= rightv - now else rightv
        # interpolate?
        # later you can up the deg of polynomial
        #return np.polyval(np.polyfit([leftt, rightt, ], [leftv, rightv, ], 1), now)
        # huh interp takes care of the case when x is outside of xp. that's nice.
        #return np.interp([now], [leftt, rightt, ], [leftv, rightv, ])[0]


if '__main__' == __name__:

    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    #print(get_tank_status())
    print(get_setpoint(force_read=True))
