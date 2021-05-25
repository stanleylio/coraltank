#
# SL2021
import time, redis, json


redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
while True:
    def f(v):
        try:
            return json.loads(v)
        except:
            return None
    d = {k:f(redis_server.get(k)) for k in 't0,setpoint,c0,hot,cold,ambient,tank_state,uptime_second,freeMB,cpu_temp'.split(',')}
    print('- - - - -')
    for k,v in d.items():
        print(f"{k} = {v}")
    
    time.sleep(3)

