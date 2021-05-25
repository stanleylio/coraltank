# SL2021
import logging, configparser, time, sys, pika, csv, sqlite3, os
from datetime import datetime, timedelta
from xmlrpc.client import ServerProxy
sys.path.append('/home/pi')
from cred import cred
from node.z import send as formatsend
from node.helper import init_rabbit, dt2ts


logger = logging.getLogger(__name__)


def get_configuration(key):
    config = configparser.ConfigParser()
    config.read('/var/www/html/config/config.txt')
    for section in config:
        try:
            return config[section][key]
        except KeyError:
            pass


def get_probe_offset():
    calibration_sample_size = max(1, int(get_configuration('calibration_sample_size')))
    with sqlite3.connect('/var/www/html/records.db') as conn:
        cur = conn.cursor()
        try:
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


def set_tank_status(status):
    assert status in {'deployed', 'maintenance'}

    now = time.time()
    nowdt = str(datetime.now())[:19]
    event = 'tank_status_change'
    message = status
    with sqlite3.connect('/var/www/html/records.db') as conn:
        cur = conn.cursor()
        cur.execute("""INSERT INTO operation ('ts','dt','e','m') VALUES (?,?,?,?)""",
                    (now, nowdt, event, message, ))


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


def trigger_valve(tank_state):
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
        logger.error(f'unknown tank_state {tank_state}')


def send(d, senderid):
    exchange = 'uhcm'
    connection, channel = init_rabbit('pi', cred['rabbitmq'])
    channel.exchange_declare(exchange=exchange, exchange_type='topic', durable=True)
    channel.basic_publish(exchange=exchange,
                          routing_key=senderid + '.s',
                          body=formatsend(None, d, src=senderid).strip(),
                          properties=pika.BasicProperties(delivery_mode=2,
                                                          content_type='text/plain',
                                                          expiration=str(7*24*3600*1000)))

def get_setpoint(*, fn='/var/www/html/config/profile.csv', force_read=True):
    """From the temperature profile, locate the temperature point with
    at timestamp closest to "now". If "now" is not covered by the
    profile, then return NaN.
    
    If you want to be fancy you can include hard-limits on the profile
    in the config.txt too. How much time do you want to spend on this
    one project? Or as a middleground, maybe use the high/low alarms as
    the limits.
    """
    
    now = time.time()

    # Timestamps in the temperature profile are interpreted as in HST.
    # Everything else on the controller uses UTC.
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
                        x = int(dt2ts(datetime.strptime(x, '%Y%m%d%H%M') - timezoneoffset))
                        y = float(y) if y != 'NA' else 'NA'
                        cur.execute(f"""INSERT INTO A ('ts', 't') VALUES (?,?)""", (x, y, ))
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
        return leftv if now - leftt <= rightv - now else rightv


if '__main__' == __name__:

    logging.basicConfig(level=logging.DEBUG)
    logger.setLevel(logging.DEBUG)

    #print(get_configuration('tank_status'))
    print(get_tank_status())
    
    print(get_setpoint(force_read=True))
    #print(get_setpoint(fn='/var/www/html/config/testprofile.csv', force_read=True))


