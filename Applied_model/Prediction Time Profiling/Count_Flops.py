'''
self.useModel boolean. If True, use ANN model, else use EOS model.

Modified for ANN:
TolSSSA increased
material balance tolerance increased

Modified for time profiling ANN. See ReadMe.

@author: markz
'''


import math
import numpy as np
from cmath import pi
import cProfile, pstats, io

# If use model
import pandas as pd
import pickle
import tensorflow as tf

# Debug
import matplotlib.pyplot as plt

class pr:
    def __init__(self):
        self.R = 8.31446261815324
        self.sqrt2 = 1.41421356237309504
        self.twosqrt2 = 2 * self.sqrt2
        self.onepsqrt2 = 2.41421356237309504
        self.onemsqrt2 = -0.41421356237309504
        self.useModel = False # By default


    ##########################################################################
    # Function definitions

    # Function bypassing problem with cubic root of small negative numbers
    def root3(self, num):
        if num < 0:
            return -(-num) ** (1. / 3.)
        else:
            return num ** (1. / 3.)


    # Returns K-values for all components using Wilson's correlation. Length of Pc, Tc, w must be equal.
    # K = y/x, x is liquid phase, y is vapor phase. Nth phase is liquid.

    def wilson_corr(self, Pr, Tr, w):
        #K = [1 / Pr[i] * math.exp(5.37 * (1 + w[i]) * (1 - 1 / Tr[i])) for i in range(len(w))]  # Truncated 5.373
        K = 1 / Pr * np.exp(5.37 * (1 + w) * (1 - 1 / Tr)) # Truncated 5.373
        # K = [1/Pr[i] * math.exp(5.373 * (1 + w[i]) * (1 - 1/Tr[i])) for i in range(len(w))]
        return K


    # Rachford-Rice
    # Get the phase compositions x, y from known K-values

    def objective_rr(self, beta, K, z):
        rrsum = ((K - 1) * z) / (1 + (K - 1) * beta)
        return np.sum(rrsum)

    # Analytical derivative of RR
    def rr_prime(self, beta, K, z):
        primesum = -1 * (z * (K - 1) ** 2) / (1 + beta * (K - 1)) ** 2
        return np.sum(primesum)


    # Solve RR equation for the vapor mole fraction beta
    #@profile
    def nr_beta(self, tol, K, beta, NRmaxit, z):
        Kmin = np.min(K)
        Kmax = np.max(K)

        # Michelsen's window
        xl = 1 / (1 - Kmax)
        xr = 1 / (1 - Kmin)
        xg = beta

        # Find root NR method
        check = 1
        i = 0
        while i < NRmaxit:
            i += 1
            y = np.sum(((K - 1) * z) / (1 + (K - 1) * xg))
            fp = np.sum(-1 * (z * (K - 1) ** 2) / (1 + xg * (K - 1)) ** 2) # This is the gradient at xg
            xn = xg - y / fp
            if xn < xl:
                xn = 0.5 * (xg + xl)
            if xn > xr:
                xn = 0.5 * (xg + xr)

            if xg != 0:
                check = abs(xn - xg)
                if check < tol:
                    break
                else:
                    xg = xn
            else:
                xg = xn

        if i > NRmaxit:
            print('Trouble in NR Solve')
            print('it = {}'.format(i))
            print('beta = {}'.format(xg))
            print('K: {}'.format(K))
            print('z: {}'.format(z))
        else:
             return xn, i


    # Analytical 3rd order polynomial solver. Modified to output sorted real roots only.
    def cubic_real_roots(self, p):
        # Input p = [A, B, C] such that x**3 + A*x**2 + B*x + C = 0

        q = (p[0]**2 - 3 * p[1]) / 9
        r = (2 * p[0]**3 - 9 * p[0] * p[1] + 27 * p[2]) / 54
        qcub = q**3
        d = qcub - r**2

        if abs(qcub) < 1E-16 and abs(d) < 1E-16:
            # 3 repeated real roots. Same as single root.
            #nroot=1
            z = np.array([-p[0] / 3])
            return z
        if abs(d) < 1E-16 or (d > 0 and abs(d) > 1E-16):
            # 3 distinct real roots
            #nroot = 3
            th = math.acos(r/math.sqrt(qcub))
            sqQ = math.sqrt(q)
            z = np.empty(3)
            z[0] = -2 * sqQ * math.cos(th/3) - p[0] / 3
            z[1] = -2 * sqQ * math.cos((th+2*pi)/3) - p[0] / 3
            z[2] = -2 * sqQ * math.cos((th+4*pi)/3) - p[0] / 3
            return z
        else:
            # 1 real root, 2 complex conjugates
            #nroots = 1
            e = self.root3(math.sqrt(-d) + abs(r))
            if r > 0:
                e = -e
            z = np.array([e + q/e - p[0]/3])
            return z

    def Z_roots_calc(self, a_mix_phase, b_mix_phase):
        A = a_mix_phase # Optimized: Already has Pr, Tr. R is cancelled.
        B = b_mix_phase
        p = [-(1 - B), (A - 3 * B ** 2 - 2 * B), -(A * B - B ** 2 - B ** 3)]
        Z_roots = self.cubic_real_roots(p)
        return Z_roots

    def Z_roots_det(self, a_mix_phase, b_mix_phase):
        '''

        :param a_mix_phase:
        :param b_mix_phase:
        :return: if multiple roots, return Z. Else, return False.
        '''
        A = a_mix_phase  # Optimized: Already has Pr, Tr. R is cancelled.
        B = b_mix_phase
        p = [-(1 - B), (A - 3 * B ** 2 - 2 * B), -(A * B - B ** 2 - B ** 3)]
        q = (p[0] ** 2 - 3 * p[1]) / 9
        r = (2 * p[0] ** 3 - 9 * p[0] * p[1] + 27 * p[2]) / 54
        qcub = q ** 3
        d = qcub - r ** 2

        if abs(d) < 1E-16 or (d > 0 and abs(d) > 1E-16):
            # 3 distinct real roots
            # nroot = 3
            th = math.acos(r / math.sqrt(qcub))
            sqQ = math.sqrt(q)
            Z = np.empty(3)
            Z[0] = -2 * sqQ * math.cos(th / 3) - p[0] / 3
            Z[1] = -2 * sqQ * math.cos((th + 2 * pi) / 3) - p[0] / 3
            Z[2] = -2 * sqQ * math.cos((th + 4 * pi) / 3) - p[0] / 3
            return Z
        else:
            return False # False = can use model

    def bm(self, phase_comps, b_i):
        return np.dot(phase_comps, b_i)

    def am(self, phase_comps, sum_xi_Aij):
        return np.dot(phase_comps, sum_xi_Aij)
    # Summation of a interactions, used in expression for lnphi

    def sum_a_interations(self, Nc, phase_comps, Am):
        sum_xi_Aij = np.zeros(Nc)
        '''
        for i in range(Nc):
            for j in range(Nc):
                sum_xi_Aij[i] += phase_comps[j] * Am[i, j]
        '''
        for i in range(Nc):
            sum_xi_Aij[i] = np.dot(phase_comps, Am[i, :])

        return sum_xi_Aij

    #@profile
    def ln_phi_calc(self, b_i, a_mix, b_mix, sum_xjAij, Z):
        # Get fugacity coeff for each component in each phase.

        a1 = b_i / b_mix * (Z - 1)
        a2 = - math.log(Z - b_mix)
        a3 = - 1 / (self.twosqrt2) * a_mix / b_mix
        a4a = sum_xjAij
        a4b = 2 / a_mix
        a4c = - b_i / b_mix
        a4 = a4a * a4b + a4c
        a5 = math.log((Z + self.onepsqrt2 * b_mix) / (Z + self.onemsqrt2 * b_mix))

        ln_phi = a1 + a2 + a3 * a4 * a5

        return ln_phi

    # Calculates mixing coefficients
    # Get a_mix_phase with mixing rule using a_i.
    def Vw(self, Nc,A,bip):
        Am = np.empty([Nc,Nc])
        for i in range(0,Nc):
            for j in range(0,Nc):
                Am[i,j] = np.sqrt(A[i] * A[j])*(1 - bip[i,j])
        return Am


    # Identify the phase stability result
    def caseid2(self, XX, itSSSAmax, TolXz, loop_count, sumXX, z):
        # Identify case
        tmp = abs(XX / z - 1)

        #tmp = [abs(XX[i] / z[i] - 1) for i in range(len(z))]
        if loop_count >= itSSSAmax:
            # Could not converge
            case_id = 1
        elif np.max(tmp) < TolXz:
            # Trivial case
            case_id = 2

        elif sumXX < 1:
            # Converged, but G of x higher than G of z
            case_id = 3
        else:
            # Two phase is more stable
            case_id = -1
            # Debug
            #print('abs(XX/z-1): {}'.format(tmp))
            #print('sumXX: {}'.format(sumXX))

        return case_id

    def two_phase_flash_iterate(self, Pr, Tr, w, SSmaxit, SStol, TolRR, Nc, Am, b_i, NRmaxit,z):
        K = self.wilson_corr(Pr, Tr, w) # If remove will have local variable clash with global
        beta = 0.5
        print(K)
        # Start looping here
        flag = 0
        outer_loop_count = 0

        ###################################
        # PROFILING
        # Single iteration of SS in two-phase flash.
        #for _ in range(100000):
        #    self.two_phase_flash_SS_test(Nc, K, flag, outer_loop_count, TolRR, b_i, Am, z)
        #return 'profiling', 'profiling'
        ###################################

        # debug
        max_c = []
        ss_loop_it = []

        while outer_loop_count < SSmaxit and flag < 2:  # Flag exit condition at 2 to print converged+1 x, y, K-values
            print('SS Flash outer loop count: ' + str(outer_loop_count))
            outer_loop_count += 1
            # Call NR method for beta (vapor fraction)
            beta, i_count = self.nr_beta(TolRR, K, beta, NRmaxit, z)

            print('Vapor frac: ' + str(beta))

            # Get Phase compositions from K and beta
            x = z / (1 + beta * (K - 1))
            y = K * x

            # Normalize
            x = x / np.sum(x)
            y = y / np.sum(y)

            # Check material balance for each component
            for comp in range(len(z)):
                if abs(z[comp] - (x[comp] * (1 - beta) + y[comp] * beta)) > 1E-6:# 1E-10 for EOS
                    print('Caution: Material balance problem for component ' + str(comp))
                    # debug
                    print(abs(z[comp] - (x[comp] * (1 - beta) + y[comp] * beta)))

            # Check mole fractions
            if 1 - np.sum(x) > 1E-12 or 1 - np.sum(y) > 1E-12:
                print('''Caution: Phase comp don't add up to 1.''')

            print('Liquid comp: ' + str(x))
            print('Vapor comp: ' + str(y))

            #####################################################
            # Liquid
            # Get parameters for Peng-Robinson EOS which are composition dependent.
            #a_mix, b_mix = ambm(x, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, x, Am)
            a_mix = self.am(x, sum_xiAij)
            b_mix = self.bm(x, b_i)

            if self.useModel:
                Z = self.Z_roots_det(a_mix, b_mix)  # If multiple roots, returns array of roots. Else, returns False.
            else:
                Z = self.Z_roots_calc(a_mix, b_mix)

            if type(Z) == bool:
                # Use ANN lnphi
                ln_phi_x = self.ln_phi_model_calc(a_mix, b_mix, b_i, sum_xiAij)
            else:
                # Use EOS lnphi
                if len(Z) > 1 and min(Z) > 0:
                    print('SA: More than 1 root. Gibb\'s minimization performed.')
                    ln_phi_x, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, x)
                else:
                    ln_phi_x = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

            ######################################################
            # Vapor
            #a_mix, b_mix = ambm(y, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, y, Am)
            a_mix = self.am(y, sum_xiAij)
            b_mix = self.bm(y, b_i)

            if self.useModel:
                Z = self.Z_roots_det(a_mix, b_mix)  # If multiple roots, returns array of roots. Else, returns False.
            else:
                Z = self.Z_roots_calc(a_mix, b_mix)

            if type(Z) == bool:
                # Use ANN lnphi
                ln_phi_y = self.ln_phi_model_calc(a_mix, b_mix, b_i, sum_xiAij)
            else:
                # Use EOS lnphi
                if len(Z) > 1 and min(Z) > 0:
                    print('SA: More than 1 root. Gibb\'s minimization performed.')
                    ln_phi_y, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, y)
                else:
                    ln_phi_y = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

            # Converge check
            ln_phi_diff = ln_phi_x - ln_phi_y
            c = np.abs(ln_phi_diff - np.log(K))

            # Store SS residual if self.return_SS_residuals exists and is True. Else pass.
            try:
                if self.return_SS_residuals:
                    self.SS_c.append(np.max(c))
                    self.SS_it.append(outer_loop_count)
            except AttributeError:
                pass

            if np.max(c) < SStol:
                flag += 1
                print('Exit flag:' + str(flag))
            else:
                flag = 0

            # debug, plot np.max(c) vs outer_loop_count
            max_c.append(np.max(c))
            ss_loop_it.append(outer_loop_count)

            # Update K
            print('K old: ' + str(K))
            K = np.exp(ln_phi_diff)
            print('K new: ' + str(K))
            print('########################################')
        print('END 2-phase flash')

        '''# debug, plot np.max(c) vs outer_loop_count
        fig = plt.figure()
        ax = fig.add_subplot(111)
        ax.plot(ss_loop_it, max_c)
        ax.set_title('SS Convergence')
        ax.set_xlabel('SS Loop Iteration')
        ax.set_ylabel('SS Residual')
        ax.set_yscale('log')
        plt.show()'''

        return x, y

    def kappa(self, w):
        kappa = []  # Verified
        for comp in range(len(w)):
            if w[comp] <= 0.49:
                kappa.append(0.37464 + 1.54226 * w[comp] - 0.26992 * w[comp] ** 2)
            else:
                kappa.append(0.37964 + w[comp] * (1.48503 + w[comp] * (-0.164423 + w[comp] * 0.016666)))
        return np.array(kappa)

    def aibi(self, P,T,w,Pr,Tr,Pc,Tc):
        PT2 = P / T ** 2
        #PT = P / T
        Kappa = self.kappa(w)
        alpha = (1 + Kappa * (1 - np.sqrt(Tr))) ** 2
        a_i = 0.457236 * alpha * Tc ** 2 * PT2 / Pc
        b_i = 0.0778 * Pr / Tr  # Optimized Bi. Tr, Pr, removed R
        return a_i, b_i

    # Outputs lower Gibbs root and corresponding ln_phi
    def checkG(self, b_i, a_mix, b_mix, sum_xiAij, Z, x):
        Zmin = min(Z)
        Zmax = max(Z)
        if Zmin < 0:
            ln_phi_max = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, Zmax)
            return ln_phi_max, Zmax

        ln_phi_min = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, Zmin)
        ln_phi_max = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, Zmax)

        arr = x * (ln_phi_min - ln_phi_max)
        if np.sum(arr) > 0:
            return ln_phi_max, Zmax
        else:
            return ln_phi_min, Zmin

    # For profiling
    def do_cprofile(self, func):
        def profiled_func(*args, **kwargs):
            profile = cProfile.Profile()
            repeats = int(1E6)
            try:
                for _ in range(repeats):
                    profile.enable()
                    result = func(*args, **kwargs)
                    profile.disable()
                return result
            finally:
                s = io.StringIO()
                sortby = 'tottime'
                ps = pstats.Stats(profile, stream=s).sort_stats(sortby)
                ps.print_stats()
                print(s.getvalue())
                print('Profiled  %d repeats. Divide by that number for per iteration times.' % (repeats))
        return profiled_func

    # SA SS single iteration standalone, for profiling only.
    # Constant variables
    #@profile
    def SA_SS_single_it(self, Nc, x, b_i, Am, XX, d, tolSSSA, exit_flag):

        sum_xiAij = self.sum_a_interations(Nc, x, Am)
        b_mix = self.bm(x, b_i)
        a_mix = self.am(x, sum_xiAij)


        if self.useModel:
            Z = self.Z_roots_det(a_mix, b_mix)  # If multiple roots, returns array of roots. Else, returns False.
        else:
            Z = self.Z_roots_calc(a_mix, b_mix)

        if type(Z) == bool:
            # Use ANN lnphi
            #ln_phi_x = self.ln_phi_model_calc(a_mix, b_mix, b_i, sum_xiAij)
            # Use npANN lnphi
            ln_phi_x = self.ln_phi_model_calc_np(a_mix, b_mix, b_i, sum_xiAij)

        else:
            # Use EOS lnphi
            if len(Z) > 1 and min(Z) > 0:
                print('SA: More than 1 root. Gibb\'s minimization performed.')
                ln_phi_x, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, x)
            else:
                ln_phi_x = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

        # Compute convergence
        tmp = np.abs(ln_phi_x + np.log(XX) - d)

        # Update XX
        XX = np.exp(d - ln_phi_x)

        # Update x
        sumXX = np.sum(XX)
        x = XX / sumXX

        # Check convergence
        if np.max(tmp) < tolSSSA:
            exit_flag += 1
        if exit_flag > 1:
            loop_count = it
            #break
        return

    # Two-phase flash SS single iteration standalone, for profiling only.
    #@profile
    def two_phase_flash_SS_test(self, Nc, K, flag, outer_loop_count, TolRR, b_i, Am, z):
        beta = 0.5
        while outer_loop_count < SSmaxit and flag < 2:  # Flag exit condition at 2 to print converged+1 x, y, K-values
            outer_loop_count += 1

            # Call NR method for beta (vapor fraction)
            beta, i_count = self.nr_beta(TolRR, K, beta, NRmaxit, z)

            # Get Phase compositions from K and beta
            x = z / (1 + beta * (K - 1))
            y = K * x

            # Normalize
            x = x / np.sum(x)
            y = y / np.sum(y)

            #####################################################
            # Liquid
            # Get parameters for Peng-Robinson EOS which are composition dependent.
            #a_mix, b_mix = ambm(x, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, x, Am)
            a_mix = self.am(x, sum_xiAij)
            b_mix = self.bm(x, b_i)

            # All EOS variables defined, solve EOS for each phase
            Z = self.Z_roots_calc(a_mix, b_mix)

            if len(Z) > 1:
                ln_phi_x, Z = self.checkG(Nc, b_i, a_mix, b_mix, sum_xiAij, Z, x)
            else:
                ln_phi_x = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, Z)
            ######################################################
            # Vapor
            #a_mix, b_mix = ambm(y, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, y, Am)
            a_mix = self.am(y, sum_xiAij)
            b_mix = self.bm(y, b_i)

            # All EOS variables defined, solve EOS for each phase
            Z = self.Z_roots_calc(a_mix, b_mix)

            if len(Z) > 1:
                ln_phi_y, Z = self.checkG(Nc, b_i, a_mix, b_mix, sum_xiAij, Z, y)
            else:
                ln_phi_y = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, Z)

            # Converge check
            ln_phi_diff = ln_phi_x - ln_phi_y
            c = np.abs(ln_phi_diff - np.log(K))
            if np.max(c) < SStol:
                flag += 1
            else:
                flag = 0

            # Update K
            K = np.exp(ln_phi_diff)
        return

    #@do_cprofile
    def stability_analysis(self, T, P, z, b_i, Am, tolSSSA, itSSSAmax, Nc, K, TolXz):
        # Get parameters for Peng-Robinson EOS which are composition dependent.
        #a_mix, b_mix = ambm(z, b_i, Am)
        sum_xiAij = self.sum_a_interations(Nc, z, Am)
        a_mix = self.am(z, sum_xiAij)
        b_mix = self.bm(z, b_i)

        Z = self.Z_roots_calc(a_mix, b_mix)
        if len(Z) > 1 and min(Z) > 0:
            print('SA: More than 1 root. Gibb\'s minimization performed.')
            ln_phi_z, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, z)
        else:
            ln_phi_z = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

        d = ln_phi_z + np.log(z)
        #################
        # Liquid-like search for instability
        XX = z / K
        x = XX / np.sum(XX) # Maybe define sumXX beforehand
        # SS in SA
        exit_flag = 0
        ###############
        # PROFILING
        # For profiling. Single iteration of SS in SA.
        for _ in range(1000):
            self.SA_SS_single_it(Nc, x, b_i, Am, XX, d, tolSSSA, exit_flag)
        return

        ###############

        # tmp (debug)
        self.liq_tmp = []
        self.liq_it = []


        for loop_count in range(int(itSSSAmax+1)):
            #a_mix, b_mix = ambm(x, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, x, Am)
            a_mix = self.am(x, sum_xiAij)
            b_mix = self.bm(x, b_i)

            if self.useModel:
                Z = self.Z_roots_det(a_mix, b_mix)  # If multiple roots, returns array of roots. Else, returns False.
            else:
                Z = self.Z_roots_calc(a_mix, b_mix)

            if type(Z) == bool:
                # Use ANN lnphi
                ln_phi_x = self.ln_phi_model_calc(a_mix, b_mix, b_i, sum_xiAij)
            else:
                # Use EOS lnphi
                if len(Z) > 1 and min(Z) > 0:
                    print('SA: More than 1 root. Gibb\'s minimization performed.')
                    ln_phi_x, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, x)
                else:
                    ln_phi_x = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

            # Compute convergence by checking stationarity
            tmp = np.abs(ln_phi_x + np.log(XX) - d)
            #print('In SA Liquid-Like: Tmp = {}'.format(tmp))
            # Log tmp (debug)
            self.liq_tmp.append(tmp)
            self.liq_it.append(loop_count)

            # Update XX
            XX = np.exp(d - ln_phi_x)

            # Update x
            sumXX = np.sum(XX)
            x = XX / sumXX

            # Check convergence
            if np.max(tmp) < tolSSSA:
                exit_flag += 1
            if exit_flag > 1:
                break

        sumXX_list = np.empty(2)
        sumXX_list[0] = sumXX
        liq_case = self.caseid2(XX, itSSSAmax, TolXz, loop_count, sumXX, z)
        #print('liq loop_count: {}'.format(loop_count))
        #################
        # Vapor-like search for instability
        XX = z * K
        x = XX / np.sum(XX)  # Maybe define sumXX beforehand

        # SS in SA
        exit_flag = 0

        # tmp (debug)
        self.vap_tmp = []
        self.vap_it = []
        for loop_count in range(int(itSSSAmax+1)):
            #a_mix, b_mix = ambm(x, b_i, Am)
            sum_xiAij = self.sum_a_interations(Nc, x, Am)
            a_mix = self.am(x, sum_xiAij)
            b_mix = self.bm(x, b_i)

            if self.useModel:
                Z = self.Z_roots_det(a_mix, b_mix)  # If multiple roots, returns array of roots. Else, returns False.
            else:
                Z = self.Z_roots_calc(a_mix, b_mix)

            if type(Z) == bool:
                # Use ANN lnphi
                ln_phi_x = self.ln_phi_model_calc(a_mix, b_mix, b_i, sum_xiAij)
            else:
                # Use EOS lnphi
                if len(Z) > 1 and min(Z) > 0:
                    print('SA: More than 1 root. Gibb\'s minimization performed.')
                    ln_phi_x, Z = self.checkG(b_i, a_mix, b_mix, sum_xiAij, Z, x)
                else:
                    ln_phi_x = self.ln_phi_calc(b_i, a_mix, b_mix, sum_xiAij, max(Z))

            # Compute convergence
            tmp = np.abs(ln_phi_x + np.log(XX) - d)
            # Log tmp (debug)
            self.vap_tmp.append(tmp)
            self.vap_it.append(loop_count)

            # Update XX
            XX = np.exp(d - ln_phi_x)

            # Update x
            sumXX = np.sum(XX)
            x = XX / sumXX

            # debug
            #print(loop_count)

            # Check convergence
            if np.max(tmp) < tolSSSA:
                exit_flag += 1
            if exit_flag > 1:
                break

        sumXX_list[1] = sumXX
        vap_case = self.caseid2(XX, itSSSAmax, TolXz, loop_count, sumXX, z)
        #print('vap loop_count: {}'.format(loop_count))

        return sumXX_list, liq_case, vap_case

    def load_ANN_model(self, modelPath, pipelinePath):
        # Loads model and transformers
        self.loaded_model_nC4 = tf.keras.models.load_model(modelPath[0])
        self.loaded_model_nC10 = tf.keras.models.load_model(modelPath[1])

        # Load pipeline
        with open(pipelinePath[0], 'rb') as f:
            self.attr_full_pipeline_nC4 = pickle.load(f)
            self.label_full_pipeline_nC4 = pickle.load(f)

        with open(pipelinePath[1], 'rb') as f:
            self.attr_full_pipeline_nC10 = pickle.load(f)
            self.label_full_pipeline_nC10 = pickle.load(f)

        print('Model loading successful.')
        return

    #@profile
    def ln_phi_model_calc(self, a_mix, b_mix, b_i, sum_xjAij):
        '''
        :param a_mix: Scalar
        :param b_mix: Scalar
        :param b_i: List of scalars, order must match components.
        :param sum_xjAij: List of scalars, order must match components.
        :return: ln_phi_i
        '''
        if not self.useModel:
            print('Error: Attempted to use model when useModel == False')
            return

        # Get prediction nC4
        X_prepared_nC4 = pd.DataFrame(np.array([a_mix, b_mix, b_i[0], sum_xjAij[0]])).T
        X_prepared_nC4.columns = ['a_mix', 'b_mix', 'b_i', 'sum']
        X_prepared_nC4 = self.attr_full_pipeline_nC4.transform(X_prepared_nC4)
        y_hat_nC4 = self.loaded_model_nC4(X_prepared_nC4)  # [0,0]
        y_hat_nC4 = self.label_full_pipeline_nC4.inverse_transform(y_hat_nC4)[0, 0]

        # Get prediction nC10
        X_prepared_nC10 = pd.DataFrame(
            np.array([a_mix, b_mix, b_i[1], sum_xjAij[1]])).T
        X_prepared_nC10.columns = ['a_mix', 'b_mix', 'b_i', 'sum']
        X_prepared_nC10 = self.attr_full_pipeline_nC10.transform(X_prepared_nC10)
        y_hat_nC10 = self.loaded_model_nC10(X_prepared_nC10)  # [0,0]
        y_hat_nC10 = self.label_full_pipeline_nC10.inverse_transform(y_hat_nC10)[0, 0]

        return np.array([y_hat_nC4, y_hat_nC10])

    def load_npANN(self):
        # Load weights and biases
        self.w_nC4, self.b_nC4 = np.load(r'C:\Users\win7\PycharmProjects\Ln_phi_model\Applied_model\numpy_relu\lnphi_nC4_T300-600_P5-100__100_4_20_100_20200916-112226\lnphi_nC4_T300-600_P5-100__100_4_20_100_20200916-112226_Konverted_weights.npz',
             allow_pickle=True)['wb']

        self.w_nC10, self.b_nC10 = np.load(
            r'C:\Users\win7\PycharmProjects\Ln_phi_model\Applied_model\numpy_relu\lnphi_nC10_T300-600_P5-100__100_4_20_100_20200915-230243\lnphi_nC10_T300-600_P5-100__100_4_20_100_20200915-230243_Konverted_weights.npz',
            allow_pickle=True)['wb']

        # Load preprocessing and post-processing (forward and inverse transforms)
        self.min_attr_nC4 = self.attr_full_pipeline_nC4.named_transformers_.num.named_steps.min_max_scaler.data_min_
        self.range_attr_nC4 = self.attr_full_pipeline_nC4.named_transformers_.num.named_steps.min_max_scaler.data_range_

        self.min_label_nC4 = self.label_full_pipeline_nC4[0].data_min_
        self.range_label_nC4 = self.label_full_pipeline_nC4[0].data_range_

        self.min_attr_nC10 = self.attr_full_pipeline_nC10.named_transformers_.num.named_steps.min_max_scaler.data_min_
        self.range_attr_nC10 = self.attr_full_pipeline_nC10.named_transformers_.num.named_steps.min_max_scaler.data_range_

        self.min_label_nC10 = self.label_full_pipeline_nC10[0].data_min_
        self.range_label_nC10 = self.label_full_pipeline_nC10[0].data_range_
        return

    #@profile
    def np_ANN_predict(self,x,w,b):
        x = np.array(x, dtype=np.float32)
        l0 = np.dot(x, w[0]) + b[0]
        l0 = np.where(l0 > 0, l0, l0 * 0.1)
        l1 = np.dot(l0, w[1]) + b[1]
        l1 = np.where(l1 > 0, l1, l1 * 0.1)
        l2 = np.dot(l1, w[2]) + b[2]
        l2 = np.where(l2 > 0, l2, l2 * 0.1)
        l3 = np.dot(l2, w[3]) + b[3]
        l3 = np.where(l3 > 0, l3, l3 * 0.1)
        l4 = np.dot(l3, w[4]) + b[4]
        return l4

    #@profile
    def ln_phi_model_calc_np(self, a_mix, b_mix, b_i, sum_xjAij):
        X_prepared_nC4 = np.array([a_mix, b_mix, b_i[0], sum_xjAij[0]])
        X_prepared_nC10 = np.array([a_mix, b_mix, b_i[1], sum_xjAij[1]])

        # Get prediction nC4
        X_prepared_nC4 = (X_prepared_nC4 - self.min_attr_nC4) / self.range_attr_nC4
        y_hat_nC4 = self.np_ANN_predict(X_prepared_nC4, self.w_nC4, self.b_nC4)
        y_hat_nC4 = self.range_label_nC4 * y_hat_nC4 + self.min_label_nC4

        # Get prediction nC10
        X_prepared_nC10 = (X_prepared_nC10 - self.min_attr_nC10) / self.range_attr_nC10
        y_hat_nC10 = self.np_ANN_predict(X_prepared_nC10, self.w_nC10, self.b_nC10)
        y_hat_nC10 = self.range_label_nC10 * y_hat_nC10 + self.min_label_nC10
        return np.array([y_hat_nC4, y_hat_nC10])




if __name__ == "__main__":
    ########################################################################################
    # INPUTS
    T = 500  # [K] [620, 650]
    P = 41#30  # [bar] [10, 30]

    # nC4-C10
    z = np.array([0.65, 0.35])
    w = np.array([0.193, 0.49])
    Pc = np.array([37.997, 21.1])  # [bar]
    Tc = np.array([425.2, 617.6])  # [K]
    BIP = np.zeros([2, 2])

    NRtol = 1E-12
    NRmaxit = 100  # I think 10 is enough
    SStol = 1E-6  #1E-10 for EOS, 1E-6 for ANN.
    tolSSSA = 1E-5
    SSmaxit = 500  # 1000000 # 1E6 might crash my computer.
    TolRR = 1E-10
    TolXz = 1E-8
    itSSSAmax = 1E6

    # More global constants
    Tr = T / Tc
    Pr = P / Pc

    Nc = len(z)

    phase_num = 1
    row_index = 0

    #####################################################################################
    # Instantiate class
    pr = pr()

    # Use Model?
    pr.useModel = True


    # Load models
    modelPath = [
        r'C:\Users\win7\Desktop\logs\logs\scalars\lnphi_nC4_T300-600_P5-100__100_4_20_100_20200916-112226',
        r'C:\Users\win7\Desktop\logs\logs\scalars\lnphi_nC10_T300-600_P5-100__100_4_20_100_20200915-230243'
    ]
    pipelinePath = [
        r'C:\Users\win7\Desktop\logs\logs\scalars\lnphi_nC4_T300-600_P5-100__100_4_20_100_20200916-112226\full_pipeline_lnphi_nC4_T300-600_P5-100__100_4_20_100_.pkl',
        r'C:\Users\win7\Desktop\logs\logs\scalars\lnphi_nC10_T300-600_P5-100__100_4_20_100_20200915-230243\full_pipeline_lnphi_nC4_T300-600_P5-100__100_4_20_100_.pkl'
    ]
    pr.load_ANN_model(modelPath, pipelinePath)

    # Load npANN weights and biases
    pr.load_npANN()

    # Parameters independent of composition placed out of loop.
    # Used in either stability analysis or 2-phase PT flash.

    # Get all K-values from Wilson
    K = pr.wilson_corr(Pr, Tr, w)
    ln_K = np.log(K)

    # Get all ai, bi values
    a_i, b_i = pr.aibi(P, T, w, Pr, Tr, Pc, Tc)

    # Get Vw mixing, part with BIPs and square roots
    Am = pr.Vw(Nc,a_i,BIP)
    ##########################################################################################
    # Debug
    pr.tmp_list = []
    pr.z_list = []
    # Stability Analysis
    # Calculate constants ln_phi(z) ln(z[i])

    sumXX_list, liq_case, vap_case = pr.stability_analysis(T, P, z, b_i, Am, tolSSSA, itSSSAmax, Nc, K, TolXz)

    # Get TPD
    TPD = -math.log(max(sumXX_list))
    print('TPD: {}'.format(TPD))
    print(sumXX_list)

    print('At P = %s bar, and T = %s K' % (P, T))
    if liq_case < 0 or vap_case < 0:
        print('Single phase unstable, TPD = %s' % TPD)
        print('Run 2-phase flash.')

        phase_num = 2
        # Now call 2-phase flash func. Return only converged composition. Optimize by re-using calculated
        # variables.

        #liq_comp, vap_comp = pr.two_phase_flash_iterate(Pr, Tr, w, SSmaxit, SStol, TolRR, Nc, Am, b_i, NRmaxit, z)
        #print('liq and vap comp:')
        #print(liq_comp, vap_comp)
        print('Skipped two_phase_flash_iterate')


    elif liq_case > 0 and vap_case > 0:
        print('Single phase stable')
        print('P = %s bar, T = %s K' % (P, T))
        print('Liq case: %d, Vap case: %d' % (liq_case, vap_case))
        # Copy single phase composition

    print('END')

