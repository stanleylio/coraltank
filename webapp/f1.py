import json, sys, logging, time, redis, os, configparser, sqlite3, requests
from flask import Flask, render_template, request, escape, Response, flash, redirect
from auth import requires_auth
from datetime import datetime
from werkzeug.utils import secure_filename
from xmlrpc.client import ServerProxy
sys.path.append('/home/pi/tankcontrol')
from common import get_setpoint, set_tank_status, get_tank_status
from eztank import get_probe_offset
sys.path.append('/home/pi')
from cred import cred


app = Flask(__name__)
app.config['UPLOAD_FOLDER'] = '/var/www/html/config'


def get_configuration(key, default=''):
    config = configparser.ConfigParser()
    fn = os.path.join(app.config['UPLOAD_FOLDER'], 'config.txt')
    config.read(fn)
    for section in config:
        try:
            return config[section][key]
        except KeyError:
            pass
    logging.warning(f'got nothing for "{key}". using default "{default}"')
    return default


'''def set_configuration(key, value):
    config = configparser.ConfigParser()
    fn = os.path.join(app.config['UPLOAD_FOLDER'], 'config.txt')
    config.read(fn)
    config['system'][key] = value
    with open(fn, 'w') as outfile:
        config.write(outfile)'''


@app.route('/', methods=['GET', 'POST'])
@requires_auth
def root():
    if 'GET' == request.method:
        controller_name = get_configuration('controller_name', '(noname)')
        serial_number = get_configuration('serial_number')
        custom_note = get_configuration('custom_note', '')
        tank_number = get_configuration('tank_number', '(not assigned to any tank)')
        cmin = get_configuration('calibration_accepted_min')
        cmax = get_configuration('calibration_accepted_max')
        panel_background_color = get_configuration('panel_background_color', default='#ffffff')
        
        return render_template('index.html',
                               nodeid=serial_number,
                               controller_name=escape(controller_name),
                               custom_profile_note=escape(custom_note),
                               tank_number=escape(tank_number),
                               cmin=cmin,
                               cmax=cmax,
                               panel_background_color=panel_background_color,
                               )
    else:
        if 'file' not in request.files:
            return redirect('/')
        
        file = request.files['file']
        if '' == file.filename:
            # user hit cancel
            return redirect('/')
        
        if file:
            if file.filename not in {'config.txt', 'profile.csv'}:
                return 'Only {config.txt, profile.csv} are accepted.'
            
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

            # parse the CSV into a db
            get_setpoint(force_read=True)
            
            return redirect('/')
    return "it's beyond my paygrade"


@app.route('/doc')
def doc():
    controller_name = get_configuration('controller_name', '(noname)')
    panel_background_color = get_configuration('panel_background_color', default='#ffffff')
    return render_template('doc.html',
                           controller_name=escape(controller_name),
                           panel_background_color=panel_background_color,
                           )
    

@app.route('/status')
def status():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)

    r = {}
    
    def f(v, *, replacement=None):
        try:
            tmp = redis_server.get(v)
            if tmp is None:
                return None
            else:
                tmp = json.loads(tmp)
                return tmp if tmp == tmp else replacement
        except:
            logging.exception(v)
        return None

    r['hot'] = f('hot')
    r['cold'] = f('cold')
    r['ambient'] = f('ambient')
    r['t0'] = f('t0')
    # if it's NA in profile.csv it's recorded as "NA" in the database
    # (putting a string in a number field, exploiting sqlite3's lack of
    # type restriction).
    r['setpoint'] = f('setpoint', replacement='NA')
    r['c0'] = f('c0')
    r['tank_state'] = f('tank_state')   # should have been "valve_state" or "valve_status". But that ship has sailed long ago.
    r['tank_status'] = get_tank_status()
    r['uptime_second'] = f('uptime_second')
    r['freeMB'] = f('freeMB')
    r['cpu_temp'] = f('cpu_temp')
    r['ts'] = time.time()
    r['inhibit'] = max(0, redis_server.ttl('inhibit'))

    return Response(json.dumps(r),
                    mimetype='application/json; charset=utf-8')
    

@app.route('/valve/<which>', methods=['GET', 'POST'])
@requires_auth
def valve(which):
    proxy = ServerProxy('http://localhost:8001/')
    
    if 'POST' == request.method:
        newstate = request.args.get('newstate')
        
        if 'on' == newstate:
            proxy.valve_on(which)
        elif 'off' == newstate:
            proxy.valve_off(which)
        else:
            return f'invalid new state {newstate}'

        return f'{which} => {newstate}'
    else:
        return Response(json.dumps(proxy.get_valve_state(which)),
                        mimetype='application/json; charset=utf-8')


@app.route('/thermostatcontrol', methods=['GET', 'POST'])
@requires_auth
def thermostatcontrol():
    if 'POST' == request.method:
        newstate = request.args.get('newstate')
        if newstate in {'deployed', 'maintenance'}:
            set_tank_status(newstate)
        else:
            return Response(json.dumps("what's the meaning of this?"),
                            mimetype='application/json; charset=utf-8')
    return Response(json.dumps(get_tank_status()),
                    mimetype='application/json; charset=utf-8')


@app.route('/pause', methods=['GET', 'POST'])
@requires_auth
def pause():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
    if 'POST' == request.method:
        redis_server.set('inhibit', 'pause', ex=15*60)
        # unconditionally shut off all valves
        proxy = ServerProxy('http://localhost:8001/')
        # no matter how many times you repeat, it's all just
        # probabilistic without actual feedback
        for _ in range(5):
            for valve in ['hot', 'cold', 'ambient']:
                #if not False == json.loads(redis_server.get(valve)):
                proxy.valve_off(valve)
            time.sleep(0.005)
        
    return json.dumps(max(0, redis_server.ttl('inhibit')))


@app.route('/resume', methods=['GET'])
@requires_auth
def resume():
    redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
    redis_server.delete('inhibit')
    return 'done'


@app.route('/beep')
@requires_auth
def beep():
    t = float(request.args.get('t', 1))
    proxy = ServerProxy('http://localhost:8001/')
    proxy.beep(t)
    return 'boop'


@app.route('/userlog', methods=['GET', 'POST'])
@requires_auth
def reference_temperature():
    if 'POST' == request.method:
        try:
            redis_server = redis.StrictRedis(host='localhost', port=6379, db=0)
            t0 = json.loads(redis_server.get('t0'))
            now = int(time.time())
            nowdt = str(datetime.now())[:19]
            try:
                tref = float(request.form['tref'])
            except:
                tref = float('nan')
            ts_user = int(request.form.get('ts_user', now))
            dt_user = str(datetime.fromtimestamp(ts_user))[:19]
            tref_note = request.form.get('tref_note', '')

            # Does NOT perform boundary check on tref. Min/max bound is
            # not the only thing we need to check to compute final
            # offset, so we defer boundary check to runtime together
            # with the others.
            if len(tref_note) > 0 or tref == tref:
                with sqlite3.connect('/var/www/html/records.db') as conn:
                    cur = conn.cursor()
                    cur.execute(f"""CREATE TABLE IF NOT EXISTS userlog (
                                    'ts' INTEGER NOT NULL,
                                    'dt' TEXT NOT NULL,
                                    'ts_user' INTEGER,
                                    'dt_user' TEXT,
                                    't0' REAL,
                                    'tref' REAL,
                                    'tref_note' TEXT
                                    )""")
                    cur.execute(f"""INSERT OR IGNORE INTO userlog ('ts', 'dt', 'ts_user', 'dt_user', 't0', 'tref', 'tref_note') VALUES (?,?,?,?,?,?,?)""",
                                (now, nowdt, ts_user, dt_user, t0, tref, tref_note, ))

                get_probe_offset(force_refresh=True)
            else:
                logging.info('The note is empty, and Tref is invalid. Ignoring this request.')
        except:
            logging.exception('wut')
        return redirect('/')
    else:   # GET
        n = request.args.get('n', 10)
        d = []
        try:
            with sqlite3.connect('/var/www/html/records.db') as conn:
                cur = conn.cursor()
                cur.execute(f"""SELECT dt,t0,tref,tref_note
                                FROM userlog
                                ORDER BY ts DESC
                                LIMIT ?""", (n, ))
                d = list(cur.fetchall())
        except:
            logging.exception('??')
        return Response(json.dumps(d),
                        mimetype='application/json; charset=utf-8')


# Ideally I'd log these (important) events into the database, but SQLite
# doesn't like multiple writers. The SD card is small so I don't want
# MySQL here either. Not to mention it probably ain't safe to write to
# disk right before shutdown.
@app.route('/nicetry', methods=['GET'])
@requires_auth
def nicetry():
    logging.exception('reboot requested')

    requests.get(f"http://pi:{cred['webapp']}@localhost:9000/index.html?processname=eztank&action=stop")
    time.sleep(0.5)
    
    proxy = ServerProxy('http://localhost:8001/')
    proxy.valve_off('hot')
    proxy.valve_off('cold')
    proxy.valve_off('ambient')
    time.sleep(0.5)
    
    ServerProxy('http://localhost:8002/').reboot()


@app.route('/tryharder', methods=['GET'])
@requires_auth
def tryharder():
    logging.exception('shutdown requested')

    requests.get(f"http://pi:{cred['webapp']}@localhost:9000/index.html?processname=eztank&action=stop")
    time.sleep(0.5)
    
    proxy = ServerProxy('http://localhost:8001/')
    proxy.valve_off('hot')
    proxy.valve_off('cold')
    proxy.valve_off('ambient')
    time.sleep(0.5)

    ServerProxy('http://localhost:8002/').shutdown()

