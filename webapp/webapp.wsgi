import sys, logging
sys.path.append('/home/pi/tankcontrol/webapp')

logging.basicConfig(stream=sys.stderr)

#print(sys.path)
from f1 import app as application
