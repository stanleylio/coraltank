import logging, time, sys, redis, json, asyncio, sqlite3, random
from datetime import datetime
sys.path.append('..')
from common import get_configuration


async def task_temperature_log():

    log_period_second = 60
    
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    def f(v):
        try:
            return json.loads(redis_server.get(v))
        except TypeError:
            return redis_server.get(v)

    while should_continue:
        await asyncio.sleep(log_period_second)
        
        keep_day = int(get_configuration('keep_local_temperature_record_days'))
        
        if keep_day > 0:
            now = int(time.time())
            nowdt = str(datetime.now())[:19]
            
            # Reminder:
            # 1. t0 is put into redis by temp_server. It is the
            #   uncorrected value from the sensors.
            # 2. get_corrected_current_temp() assumes the t0 values
            #   stored in the local db are uncorrected values.
            #
            # That's unfortunate: the variable reported to the UH site
            # is also named "t0", but that one is the corrected value
            # (see get_corrected_current_temp()). Should have named it
            # "t0c" or something.
            try:
                t0 = f('t0')
                t0c = f('t0c')
                setpoint = f('setpoint')

                logging.info(f"{setpoint}, {t0}, {t0c}")

                conn = sqlite3.connect('/var/www/html/records.db')
                if random.random() > 0.9999:
                    conn.execute('VACUUM')
                with conn as conn:
                    cur = conn.cursor()
                    cur.execute(f"""CREATE TABLE IF NOT EXISTS tank_temperature (
                                    'ts' INTEGER PRIMARY KEY,
                                    'dt' TEXT NOT NULL,
                                    't0' REAL,
                                    't0c' REAL,
                                    'setpoint' REAL
                                    )""")
                    if random.random() > 0.99:
                        cur.execute(f"""DELETE FROM tank_temperature WHERE ts < ?""", (now - keep_day*24*3600, ))
                    cur.execute(f"""INSERT OR IGNORE INTO tank_temperature ('ts', 'dt', 't0', 't0c', 'setpoint') VALUES (?,?,?,?,?)""", (now, nowdt, t0, t0c, setpoint, ))
            except KeyboardInterrupt:
                raise
            except:
                # catch all else and quit
                logging.exception('?')
                break


if '__main__' == __name__:
    
    logging.basicConfig(level=logging.INFO)
    logging.getLogger('common').setLevel(logging.WARNING)

    should_continue = True

    async def main():
        await asyncio.gather(
            task_temperature_log(),
            )
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        should_continue = False
        logging.info('user interrupted')
