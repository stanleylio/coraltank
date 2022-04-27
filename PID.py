import time, random, sys, logging
import numpy as np


# Notes:
# The goal here is to hide the temporary variables for I and D
# calculation; there is not much computation to speak of.
#
# Instead of class, you could also use send and yield.


logger = logging.getLogger(__name__)


class PID:
    def __init__(self, K, this_many, this_old):
        self.K = K
        self.this_many = this_many
        self.this_old = this_old
        self.e = []
        logger.debug(f"{K}, keep {this_many}, expire in {this_old}s")

    # in practice this is what you actually use
    def get_weighted_sum(self, error, *, now=None):
        #return np.dot(self.K, self.update(error, now=now))
        X = self.update(error, now=now)
        logger.debug(f"{self.K[0]*X[0]:.4f} + {self.K[1]*X[1]:.4f} + {self.K[2]*X[2]:.4f}")
        return np.dot(self.K, X)

    # keeping the three numbers before weighting and summation for
    # simulation use
    def update(self, error, *, now=None):
        now = time.time() if now is None else now
        self.e.append((now, error, ))
        self._housekeeping(now=now)

        if len(self.e) >= 2:
            # I don't trust instantaneous samples from the probes. If I
            # have the memory to keep multiple samples, why not use them
            # all instead of just the most recent two.
            x,y = zip(*self.e)
            ei = np.trapz(y, x=x)
            ed = np.polyfit(x, y, 1)[0]
            r = (error, ei, ed, )
        else:
            r = (error, 0, 0, )

        # reminder: this is prior to the weighting. the PID parameters
        # are not factored in yet.
        #logger.debug(f"{r[0]:.4f}, {r[1]:.4f}, {r[2]:.4f}")
        return r

    def change_K(self, newK):
        self.K = newK

    def _housekeeping(self, *, now=None):
        # "Discard records if: too old, or too many"
        now = time.time() if now is None else now
        self.e = [ee for ee in self.e if now - ee[0] <= self.this_old]
        self.e = self.e[-self.this_many:]
        # This means you won't have the "wind up" problem, but this also
        # means that you're not really following that "I += e(t)"
        # definition. Also now your I term is affected by the size of
        # your history queue.
        # Alternatively you could compute your new estimate by weighting
        # with the previous estimate, in which case you'd only need to
        # keep the most recent estimate. By hey compute is free.


if '__main__' == __name__:

    import matplotlib.pyplot as plt

    K = (2, 0.1, 0.1, )
    pid = PID(K, 10, 2*60)

    ts = np.linspace(0, 60, 100)
    SP = 2.0*np.sin(0.5*ts) + 27 + 0.01*np.random.rand(*ts.shape)
    #PV = np.linspace(setpoint - 1, setpoint + 1, len(ts)) + 0.01*np.random.rand(*ts.shape) - 0.05

    Ks = []
    PV = []
    pv = 23
    y = 0
    #for tss,pv in zip(ts, PV):
    for tss,sp in zip(ts, SP):
        # "plant response to input y"
        pv += 0.4*y + 0.01*random.random() - 0.005
        
        PV.append(pv)
        x = np.array(pid.update(sp - pv, now=tss))
        Ks.append(x)
        y = np.dot(K, x)

    p,i,d = zip(*Ks)

    fig, axes = plt.subplots(3, 1, figsize=(16,9), sharex=True)

    axes[0].plot(ts, SP, '-', label='SP')
    axes[0].plot(ts, PV, '.', label='PV')
    axes[1].plot(ts, SP - PV, label='SP - PV')
    axes[2].plot(ts, p, label='p')
    axes[2].plot(ts, i, label='i')
    axes[2].plot(ts, d, label='d')
    [ax.legend() for ax in axes]

    plt.show()
