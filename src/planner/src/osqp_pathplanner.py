#!/usr/bin/env python


##########################################################
##      IRI 1:10 Autonomous Car                         ##
##      Supervisor: Puig Cayuela Vicenc                 ##
##      Author:     Shivam Chaubey                      ##
##      Email:      shivam.chaubey1006@gmail.com        ##
##      Date:       18/05/2021                          ##
##########################################################

from scipy import sparse
import numpy as np
from numpy import hstack, inf, ones
import rospy
from math import cos, sin, atan, pi, isnan
# from math import isnan
# from numpy import cos, sin, pi

import osqp


np.set_printoptions(formatter={'float': lambda x: "{0:0.3f}".format(x)})

class Path_planner_MPC:
    """Create the Path Following LMPC controller with LTV model
    Attributes:
        solve: given x0 computes the control action
    """
    def __init__(self, N, vx_ref, dt, map):


        # Vehicle parameters:
        self.m          = rospy.get_param("m")
        self.rho        = rospy.get_param("rho")
        self.lr         = rospy.get_param("lr")
        self.lf         = rospy.get_param("lf")
        self.Cm0        = rospy.get_param("Cm0")
        self.Cm1        = rospy.get_param("Cm1")
        self.C0         = rospy.get_param("C0")
        self.C1         = rospy.get_param("C1")
        self.Cd_A       = rospy.get_param("Cd_A")
        self.Caf        = rospy.get_param("Caf")
        self.Car        = rospy.get_param("Car")
        self.Iz         = rospy.get_param("Iz")

        # bound on states and inputs
        self.vx_max     = rospy.get_param("max_vel")
        self.vx_min     = rospy.get_param("min_vel")
        self.duty_max   = rospy.get_param("dutycycle_max")
        self.duty_min   = rospy.get_param("dutycycle_min")
        self.str_max    = rospy.get_param("steer_max")
        self.str_min    = rospy.get_param("steer_min")
        self.ey_max     = rospy.get_param("lat_e_max")
        self.etheta_max = rospy.get_param("orient_e_max")

        # bounds on steering and dutycycle changes
        self.dstr_max   = self.str_max*0.5
        self.dstr_min   = self.str_min*0.5
        self.dduty_max  = self.duty_max*0.2
        self.dduty_min  = self.duty_min*0.2

        # Normalize the weight to be used as normalized objective function
        vx_scale        = 1/((self.vx_max-self.vx_min)**2)
        duty_scale      = 1/((self.duty_max-self.duty_min)**2)
        str_scale       = 1/((self.str_max-self.str_min)**2)
        ey_scale        = 1/((self.ey_max+self.ey_max)**2)
        etheta_scale    = 1/((self.etheta_max+self.etheta_max)**2)
        dstr_scale      = 1/((self.dstr_max-self.dstr_min)**2)
        dduty_scale     = 1/((self.dduty_max-self.dduty_min)**2)


        # Form the array for the bounds
        self.xmin       = np.array([self.vx_min, -10., -100.,  -self.ey_max, -100])
        self.xmax       = np.array([self.vx_max, 10., 100., self.ey_max, 100])
        self.umin       = np.array([self.str_min, self.duty_min]) 
        self.umax       = np.array([self.str_max, self.duty_max])
        self.dumin      = np.array([self.dstr_min, self.dduty_min]) 
        self.dumax      = np.array([self.dstr_max, self.dduty_max])


        ######################################## MPC parameter setup #####################################################

        # Introduce the integral action (slew_rate_on = True) and soft constraints (soft_constraints_on = True) if needed.
        self.slew_rate_on = rospy.get_param("/trajectory_planner/integral_action")
        self.soft_constraints_on = rospy.get_param("/trajectory_planner/soft_constraints")


        # Assign the weight of objective function
        # self.Q  = 0.6 * np.array([0.4*vx_scale, 0.0, 0.00, 0.05*etheta_scale, 0.0, 0.55*ey_scale]) # penality on states 
        # self.R  = 0.1 * np.array([0.0*str_scale, 0.05*duty_scale])     # Penality on input (dutycycle, steer)
        

        # self.Q  = 0.8 * np.array([0.6*vx_scale, 0.0, 0.00, 0.05*etheta_scale, 0.0, 0.35*ey_scale]) # penality on states 
        # self.R  = 0.1 * np.array([0.0*str_scale, 0.05*duty_scale])     # Penality on input (dutycycle, steer)
        
        # self.Q  = 0.8 * np.array([0.6*vx_scale, 0.000000, 0.00, 0.2*ey_scale, 0.1*etheta_scale]) # penality on states 
        # self.R  = 0.1 * np.array([0.01*str_scale, 0.01*duty_scale])     # Penality on input (dutycycle, steer)
        

        self.Q  = 0.8 * np.array([0.6*vx_scale, 0.000000, 0.01, 0.3*ey_scale, 0.01*etheta_scale]) # penality on states 
        self.R  = 0.01 * np.array([0.01*str_scale, 0.01*duty_scale])     # Penality on input (dutycycle, steer)
        

        self.dR = 0.001 * np.array([0.09*dstr_scale,0.01*dduty_scale])  # Penality on Input rate 
        self.Qe = np.array([1, 0, 0, 1, 1])*(10.0e8) # Penality on soft constraints 

        
        # Create an OSQP object
        self.prob = osqp.OSQP()

        # Formulate the MPC problem
        self.N     = N
        self.nx    = self.Q.shape[0]
        self.nu    = self.R.shape[0]
        self.vx_ref   = vx_ref # velocity desired to be achieved

        self.LinPoints = np.zeros((self.N+2,self.nx))   
        self.xPred = []                                 # states predicted from MPC solution                
        self.uPred = []                                 # input predicted from MPC solution    
        self.dt = dt                                    # update rate or sampling time
        self.uminus1 = np.array([0.1,0.1]).T                # Past input which will be used when integral action is needed
  
        self.map = map                                  # offline track which has to be followed by the vehicle. 
        self.halfWidth = map.halfWidth                  # width of the track

        self.feasible = 1


    def MPC_setup(self, A_vec, B_vec, u, x0, vel_ref):

        '''
        MPC formulation setup >>>
        Objective function:: Ju = JQx + JQu + JQdu + JQe  
        
        where,
        JQx  : Cost of states 
        JQu  : Cost of input
        JQdu : Cost of input rate
        JQe  : Cost of soft constraints
        
        Cost function changed to QP problem:
        min 1/2(z^T.P.z) + q^Tz
        subject to:
            l =< Az <= u

        The sparse matrix for the nonlinear dynamics
        (A_vec, B_vec) is set to '1' at all places where the values are going
        to be changed. In the further update step this value will be updated.
        During the update the A matrix will be replaced with the new one by
        passing only the required value at those location. Follow this link
        for further information on how to update sparse matrix using OSQP
        (https://groups.google.com/g/osqp/c/ZFvblAQdUxQ). 
        '''

        [N,nx,nu] = self.N, self.nx, self.nu 

        xr = np.array([vel_ref,0.,0.,0.,0.])
        u_ref = np.array([0.0,0.0])
    
        #################### P formulation ################################
        Q  = sparse.diags(self.Q)
        QN = Q
        R  = sparse.diags(self.R)
        dR = sparse.diags(self.dR)
        Qeps  = sparse.diags(self.Qe)
        

        PQx = sparse.block_diag([sparse.kron(sparse.eye(N), Q), QN], format='csc')
        PQu = sparse.kron(sparse.eye(N), R)
        idu = (2 * np.eye(N) - np.eye(N, k=1) - np.eye(N, k=-1))
        PQdu = sparse.kron(idu, dR)
        PQeps = sparse.kron(sparse.eye(N+1), Qeps)
        
            
        #################### q formulation ################################
        qQx  = np.hstack([np.kron(np.ones(N), -Q.dot(xr)), -QN.dot(xr)])
        qQu  = np.kron(np.ones(N), -R.dot(u_ref))
        qQdu = np.hstack([-dR.dot(self.uminus1), np.zeros((N - 1) * nu)])
        qQeps = np.zeros((N+1)*nx)

        
        '''Objective function formulation'''
        if self.soft_constraints_on and self.slew_rate_on:
            self.P = sparse.block_diag([PQx, PQu + PQdu, PQeps], format='csc')
            self.q = np.hstack([qQx, qQu + qQdu, qQeps])

        elif self.slew_rate_on:
            self.P = sparse.block_diag([PQx, PQu + PQdu], format='csc')
            self.q = np.hstack([qQx, qQu + qQdu])

        elif self.soft_constraints_on:
            self.P = sparse.block_diag([PQx, PQu, PQeps], format='csc')
            self.q = np.hstack([qQx, qQu, qQeps])

        else:
            self.P = sparse.block_diag([PQx, PQu], format='csc')
            self.q = np.hstack([qQx, qQu])
            
        
        '''Equality constraints : LPV dynamics'''
        A_tr = sparse.lil_matrix((nx*len(A_vec)+nx, nx*len(A_vec)+nx))
        for i in range(1,len(A_vec)+1):
            A_tr[nx*i:nx*i+nx,(i-1)*nx:(i-1)*nx+ nx] = A_vec[i-1]

        B_tr = sparse.lil_matrix((nx*len(B_vec)+nx, nu*len(B_vec)))
        for i in range(1,len(B_vec)+1):
            B_tr[nx*i:nx*i+nx,(i-1)*nu:(i-1)*nu+ nu] = B_vec[i-1]
        
        Ax = sparse.kron(sparse.eye(N+1),-sparse.eye(nx)) + A_tr
        Bu = B_tr
        
        n_eps = (N + 1) * nx
        '''Equality constraints: LPV dynamics and initial states x0'''
        self.Aeq = sparse.hstack([Ax, Bu]).tocsc()
        if self.soft_constraints_on:
            self.Aeq = sparse.hstack([self.Aeq, sparse.csc_matrix((self.Aeq.shape[0], n_eps))])

        self.leq = np.hstack([-x0, np.zeros(N*nx)])
        self.ueq = self.leq
                                                      
        '''Inequality constraints: bound on states'''
        self.Aineq_x = sparse.hstack([sparse.eye((N + 1) * nx), sparse.csc_matrix(((N+1)*nx, N*nu))])
        if self.soft_constraints_on:
            self.Aineq_x = sparse.hstack([self.Aineq_x, sparse.eye(n_eps)]) # For soft constraints slack variables
        self.lineq_x = np.kron(np.ones(N + 1), self.xmin) # lower bound of inequalities on states
        self.uineq_x = np.kron(np.ones(N + 1), self.xmax) # upper bound of inequalities on states

        '''Inequality constraints: bound on input'''
        self.Aineq_u = sparse.hstack([sparse.csc_matrix((N*nu, (N+1)*nx)), sparse.eye(N * nu)])
        if self.soft_constraints_on:
            self.Aineq_u = sparse.hstack([self.Aineq_u, sparse.csc_matrix((self.Aineq_u.shape[0], n_eps))]) # For soft constraints slack variables
        self.lineq_u = np.kron(np.ones(N), self.umin)     # lower bound of inequalities on input
        self.uineq_u = np.kron(np.ones(N), self.umax)     # upper bound of inequalities on input

        # Inequality constraints: bounds on du (input rate)
        if self.slew_rate_on == True:
            self.Aineq_du = sparse.vstack([sparse.hstack([np.zeros((nu, (N + 1) * nx)), sparse.eye(nu), np.zeros((nu, (N - 1) * nu))]),  # for u0 - u-1
                                      sparse.hstack([np.zeros((N * nu, (N+1) * nx)), -sparse.eye(N * nu) + sparse.eye(N * nu, k=1)])  # for uk - uk-1, k=1...Np
                                      ]
                                     )
            if self.soft_constraints_on:
                self.Aineq_du = sparse.hstack([self.Aineq_du, sparse.csc_matrix((self.Aineq_du.shape[0], n_eps))])
            self.uineq_du = np.kron(np.ones(N+1), self.dumax)   # upper bound of inequalities on input rate 
            self.uineq_du[0:nu] += self.uminus1          # Equality constraint on previous input uminus_1
            self.lineq_du = np.kron(np.ones(N+1), self.dumin)   # lower bound of inequalities on input rate
            self.lineq_du[0:nu] += self.uminus1           # Equality constraint on previous input uminus_1


        #################### Combining equivalent and inequivalent constraints ###################
        if self.slew_rate_on: 
            self.A = sparse.vstack([self.Aeq, self.Aineq_x, self.Aineq_u, self.Aineq_du]).tocsc()
            self.l = np.hstack([self.leq, self.lineq_x, self.lineq_u, self.lineq_du])
            self.u = np.hstack([self.ueq, self.uineq_x, self.uineq_u, self.uineq_du])

        else:
            self.A = sparse.vstack([self.Aeq, self.Aineq_x, self.Aineq_u]).tocsc()
            self.l = np.hstack([self.leq, self.lineq_x, self.lineq_u])
            self.u = np.hstack([self.ueq, self.uineq_x, self.uineq_u])


        ##### vector of non zero element is needed to update the sparse matrix #####
        At = self.A.transpose(copy=True) 
        At.sort_indices()
        (self.col_indices, self.row_indices) = At.nonzero()


 

        #################################### Problem Setup ###################################
        self.prob.setup(self.P, self.q, self.A, self.l, self.u, warm_start=True, polish=True, verbose = False)
        

     
    def MPC_update(self, A_vec, B_vec, x0):
        '''
        Input: LPV Model(A_vec, B_vec) , current estimated state states (x0), previous input if slew_rate == on 
        Ouput: Update the MPO formulation
        '''

        [N,nx,nu] = self.N, self.nx, self.nu 

        '''Equality constraints LPV dynamics'''
        A_tr = sparse.lil_matrix((nx*len(A_vec)+nx, nx*len(A_vec)+nx))
        for i in range(1,len(A_vec)+1):
            A_tr[nx*i:nx*i+nx,(i-1)*nx:(i-1)*nx+ nx] = A_vec[i-1]

        B_tr = sparse.lil_matrix((nx*len(B_vec)+nx, nu*len(B_vec)))
        for i in range(1,len(B_vec)+1):
            B_tr[nx*i:nx*i+nx,(i-1)*nu:(i-1)*nu+ nu] = B_vec[i-1]
        
        Ax = sparse.kron(sparse.eye(N+1),-sparse.eye(nx)) + A_tr
        Bu = B_tr
        self.Aeq = sparse.hstack([Ax, Bu]).tocsc()
        if self.soft_constraints_on:
            self.Aeq = sparse.hstack([self.Aeq, sparse.csc_matrix((self.Aeq.shape[0], (N + 1) * nx))])

        ############### Update equality constraints ##############        
        self.A[:self.Aeq.shape[0], :self.Aeq.shape[1]] = self.Aeq
        self.l[:nx] = -x0
        self.u[:nx] = -x0

        ########### Update inequality constraint if slew_rate == on with previous input (uminus_1)##############
        if self.slew_rate_on == True:
            self.l[(N+1)*nx + (N+1)*nx + (N)*nu:(N+1)*nx + (N+1)*nx + (N)*nu + nu] = self.dumin + self.uminus1  # update constraint on \Delta u0: Dumin <= u0 - u_{-1}
            self.u[(N+1)*nx + (N+1)*nx + (N)*nu:(N+1)*nx + (N+1)*nx + (N)*nu + nu] = self.dumax + self.uminus1  # update constraint on \Delta u0: u0 - u_{-1} <= Dumax

        Ax_value = self.A[self.row_indices, self.col_indices].A1
        # Ax_value = self.A[self.col_indices, self.row_indices].A1

        self.prob.update( Ax = Ax_value , l= self.l, u= self.u)


    def MPC_solve(self):
        '''
        Solve the QP problem 
        '''
        [N,nx,nu] = self.N, self.nx, self.nu 

        res = self.prob.solve()
        # Check solver status
        if res.info.status != 'solved' or isnan(res.info.obj_val):
            print 'OSQP did not solve the problem! , obj val = {}'.format(res.info.obj_val)
            print "Objective value", res.info.obj_val
        
            self.feasible = 0

        Solution = res.x
        
        # print "controller to be applied", Solution[(N+1)*nx:(N+1)*nx + nu]
        # print "Solution shape", Solution.shape
        
        self.xPred = np.squeeze(np.transpose(np.reshape((Solution[np.arange(nx * (N + 1))]), (N + 1, nx)))).T
        self.uPred = np.squeeze(np.transpose(np.reshape((Solution[nx * (N + 1) + np.arange(nu * N)]), (N, nu)))).T

        # print 'Solution', Solution


    def LPVPrediction(self, x, SS, u):

        m = self.m
        rho = self.rho
        lr = self.lr
        lf = self.lf
        Cm0 = self.Cm0
        Cm1 = self.Cm1
        C0 = self.C0
        C1 = self.C1
        Cd_A = self.Cd_A
        Caf = self.Caf
        Car = self.Car
        Iz = self.Iz

        # epss= self.epss

        STATES_vec = np.zeros((self.N, self.nx))

        Atv = []
        Btv = []
        Ctv = []

        for i in range(0, self.N):
            if i==0:
                states  = np.reshape(x, (self.nx,1))
                # print "states", states

            vx      = float(states[0])
            vy      = float(states[1])
            omega   = float(states[2])
            ey      = float(states[3])
            epsi    = float(states[4])

            # print "epsi", epsi
            PointAndTangent = self.map.PointAndTangent         
            cur     = Curvature(SS[i], PointAndTangent)

            delta   = float(u[i,0])  
            dutycycle   = float(u[i,1])  
            
            # print "delta", delta, "duty", u[i,1] 

            # if abs(dutycycle) <= 0.05:
            #     vx = 0.0
            #     vy = 0.0
            #     omega = 0.0


            A11 = 0.0
            A12 = 0.0
            A13 = 0.0
            A22 = 0.0
            A23 = 0.0
            A32 = 0.0
            A33 = 0.0
            B31 = 0.0
            eps = 0.0
            if abs(vx) == 0.0:
                eps = 0.0001
            else:
                eps = 0.0
            ## et to not go to nan
            # if abs(vx) > 0.0001:  
            A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
            A12 = 2*Caf*sin(delta)/(m*(vx+eps)) 
            A13 = 2*Caf*lf*sin(delta)/(m*(vx+eps)) + vy
            A22 = -(2*Car + 2*Caf*cos(delta))/(m*(vx+eps))
            A23 = (2*Car*lr - 2*Caf*lf*cos(delta))/(m*(vx+eps)) - vx
            A32 = (2*Car*lr - 2*Caf*lf*cos(delta))/(Iz*(vx+eps))
            A33 = -(2*Car*lf*lf*cos(delta) + 2*Caf*lr*lr)/(Iz*(vx+eps))
            B31 = 2*Caf*lf*cos(delta)/(Iz*(vx+eps))


            # print '\n A11',A11,'A12',A12,'A13',A13,'A22',A22,'A23',A23,'A32',A32,'A33',A33,'B31',B31
            # print "\n cur",cur, "epsi", epsi, "ey", ey, 'vx', vx, 'vy', vy, 'omega', omega
            A41 = -(cur*cos(epsi))/(1-ey*cur)
            A42 = (cur*sin(epsi))/(1-ey*cur)
            A51 = cos(epsi)/(1-ey*cur)
            A52 = -sin(epsi)/(1-ey*cur)
            A61 = sin(epsi)
            A62 = cos(epsi)
            B11 = -(2*Caf*sin(delta))/m
            B12 = (Cm0 - Cm1*vx)/m
            B21 = 2*Caf*cos(delta)/m


            A1      = (1/(1-ey*cur)) 
        # print "epsi", epsi
        # print 'cur', cur
            A2      = np.sin(epsi)
            A4 = vx

            # print "\nA1", A1, "A2",A2, "A4", A4 
            Ai = np.array([ [A11    ,  A12 ,  A13 ,  0., 0. ],   # [vx]
                            [ 0     ,  A22 ,  A23  , 0., 0. ],   # [vy]
                            [ 0     ,  A32 ,  A33  , 0., 0. ],   # [wz]
                            [0    ,  1 ,   0 ,   0., A4 ],   # [ey]
                            [-A1*cur    ,  A1*A2*cur ,   1 ,  0., 0. ]])  # [epsi] 

            

            # Ai = np.array([ [A11    ,  A12 ,  A13 ,  0., 0. ],   # [vx]
            #                 [ 0     ,  A22 ,  A23  , 0., 0. ],   # [vy]
            #                 [ 0     ,  A32 ,  A33  , 0., 0. ],   # [wz]
            #                 [A61    ,  A62 ,   0. ,   0., 0 ],   # [ey]
            #                 [A41    ,  A42 ,   1. ,  0., 0. ]])  # [epsi] 


            Bi  = np.array([[ B11, B12 ], #[steer, dutycycle
                            [ B21, 0. ],
                            [ B31, 0. ],
                            [ 0.,   0. ],
                            [ 0.,   0. ]])

            Ci  = np.array([[ 0. ],
                            [ 0. ],
                            [ 0. ],
                            [ 0. ],
                            [ 0. ]])

   

            Ai = np.eye(len(Ai)) + self.dt * Ai
            Bi = self.dt * Bi
            Ci = self.dt * Ci

            states_new = np.dot(Ai, states) + np.dot(Bi, np.transpose(np.reshape(u[i,:],(1,2))))

            STATES_vec[i] = np.reshape(states_new, (self.nx,))

            states = states_new

            Atv.append(Ai)
            Btv.append(Bi)
            Ctv.append(Ci)

        # return STATES_vec, Atv, Btv, Ctv
        return Atv, Btv, Ctv



    def LPVPrediction_old(self, x, SS, u):
        '''
        Obtain the LPV model along the horizon (n) using the current state x(i) and set of predicted control input at previous step u(i-1)
        '''
        #############################################
        ## States:
        ##   long velocity    [vx]
        ##   lateral velocity [vy]
        ##   angular velocity [wz]
        ##   theta error      [epsi]
        ##   distance traveled[s]
        ##   lateral error    [ey]
        ##
        ## Control actions:
        ##   Steering angle   [delta]
        ##   Acceleration     [a]
        ##
        ## Scheduling variables:
        ##   vx, vy, epsi, ey, cur
        #############################################

        m     =   self.m;
        rho   =   self.rho;
        lr    =   self.lr;
        lf    =   self.lf;
        Cm0   =   self.Cm0;
        Cm1   =   self.Cm1;
        C0    =   self.C0;
        C1    =   self.C1;
        Cd_A  =   self.Cd_A;
        Caf   =   self.Caf;
        Car   =   self.Car;
        Iz    =   self.Iz;

         

        STATES_vec = np.zeros((self.N, 5))

        Atv = []
        Btv = []
        Ctv = []


        for i in range(0, self.N):

            if i==0:
                states  = np.reshape(x, (5,1))

            vx      = float(states[0])
            vy      = float(states[1])
            omega   = float(states[2])
            epsi    = float(states[4])
            ey      = float(states[3])

            PointAndTangent = self.map.PointAndTangent         
            cur     = Curvature(SS[i], PointAndTangent)

            
            # if i == (self.N - 1):
            #     ey = 0
            #     epsi = 0

            delta = float(u[i,0])
            dutycycle = float(u[i,1])



            F_flat = 0;
            Fry = 0;
            Frx = 0;
            
            A31 = 0;
            A11 = 0;


            eps = 0.00000
            # if abs(vx)> 0.0:

            # F_flat = 2*Caf*(delta - ((vy+lf*omega)/(vx+eps)));        
            # Fry = -2*Car*((vy - lr*omega)/(vx+eps)) ;

            F_flat = 2*Caf*(delta- atan((vy+lf*omega)/(vx+eps)));        
            Fry = -2*Car*atan((vy - lr*omega)/(vx+eps)) ;
            A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
            A31 = -Fry*lr/((vx+eps)*Iz);
                
            A12 = omega;
            A21 = -omega;
            A22 = 0;
            
            # if abs(vy) > 0.0:
            A22 = Fry/(m*(vy+eps));

            B11 = 0;
            B31 = 0;
            B21 = 0;
            
            # if abs(delta) > 0:
            B11 = -F_flat*sin(delta)/(m*(delta+eps));
            B21 = F_flat*cos(delta)/(m*(delta+eps));    
            B31 = F_flat*cos(delta)*lf/(Iz*(delta+eps));


            B12 = (1/m)*(Cm0 - Cm1*vx);


            A41 = -(cur*cos(epsi))/(1-ey*cur)
            A42 = (cur*sin(epsi))/(1-ey*cur)

            # A51 = (1/(1-ey*cur)) * ( -cos(epsi) * cur )
            # A52 = (1/(1-ey*cur)) * ( +sin(epsi)* cur )
            A61 = cos(epsi) / (1-ey*cur)
            A62 = -sin(epsi) / (1-ey*cur) #should be mulitplied by -1
            A7  = sin(epsi) 
            A8  = cos(epsi)
            print "\n cur",cur, "epsi", epsi, "ey", ey, 'vx', vx, 'vy', vy, 'omega', omega


            Ai = np.array([ [A11    ,  A12 ,   0. ,  0., 0.],  # [vx]
                            [A21    ,  A22 ,   0  ,  0., 0.],  # [vy]
                            [A31    ,   0 ,    0  ,  0., 0.],  # [wz]
                            [A61    ,  A62 ,   0. ,  0., 0., ], # [ey]
                            [A41   ,  A42 ,   1. ,   0., 0, ]])  # [epsi]


            print "Ai", Ai


        #     A11 = 0.0
        #     A12 = 0.0
        #     A13 = 0.0
        #     A22 = 0.0
        #     A23 = 0.0
        #     A32 = 0.0
        #     A33 = 0.0
        #     B31 = 0.0

        #     eps = 0.00
        #     ## et to not go to nan
        #     if abs(vx) > 0.001:  
        #         A11 = -(1/m)*(C0 + C1/(eps+vx) + Cd_A*rho*vx/2);
        #         A12 = 2*Caf*sin(delta)/(m*(vx+eps)) 
        #         A13 = 2*Caf*lf*sin(delta)/(m*(vx+eps)) + vy
        #         A22 = -(2*Car + 2*Caf*cos(delta))/(m*(vx+eps))
        #         A23 = (2*Car*lr - 2*Caf*lf*cos(delta))/(m*(vx+eps)) - vx
        #         A32 = (2*Car*lr - 2*Caf*lf*cos(delta))/(Iz*(vx+eps))
        #         A33 = -(2*Car*lf*lf*cos(delta) + 2*Caf*lr*lr)/(Iz*(vx+eps))
        #         B31 = 2*Caf*lf*cos(delta)/(Iz*(vx+eps))

        #     A41 = -(cur*cos(epsi))/(1-ey*cur)
        #     A42 = (cur*sin(epsi))/(1-ey*cur)
        #     A51 = cos(epsi)/(1-ey*cur)
        #     A52 = -sin(epsi)/(1-ey*cur)
        #     A61 = sin(epsi)
        #     A62 = cos(epsi)
        #     B11 = -(2*Caf*sin(delta))/m
        #     B12 = (Cm0 - Cm1*vx)/m
        #     B21 = 2*Caf*cos(delta)/m


        #     A1      = (1/(1-ey*cur)) 
        # # print "epsi", epsi
        # # print 'cur', cur
        #     A2      = np.sin(epsi)
        #     A4 = vx

            # Ai = np.array([ [A11    ,  A12 ,  A13 ,  0., 0. ],   # [vx]
            #                 [ 0     ,  A22 ,  A23  , 0., 0. ],   # [vy]
            #                 [ 0     ,  A32 ,  A33  , 0., 0. ],   # [wz]
            #                 [0    ,  1 ,   0 ,   0., A4 ],   # [ey]
            #                 [-A1*cur    ,  A1*A2*cur ,   1 ,  0., 0. ]])  # [epsi] 

            

            # Ai = np.array([ [A11    ,  A12 ,  A13 ,  0., 0. ],   # [vx]
            #                 [ 0.     ,  A22 ,  A23  , 0., 0. ],   # [vy]
            #                 [ 0.     ,  A32 ,  A33  , 0., 0. ],   # [wz]
            #                 [-A1*cur    ,  A1*A2*cur ,   1. ,   0., 0 ],   # [epsi]
            #                 [A51    ,  A52 ,   1 ,  0., 0. ],   # [s] 
            #                 [0.    ,  1 ,   0. ,  0., A4 ]])  # [ey] 


            Bi  = np.array([[ B11, B12 ], #[delta, a]
                            [ B21, 0 ],
                            [ B31, 0 ],
                            [ 0,   0 ],
                            [ 0,   0 ]])

            # print "Bi", Bi

            # Ai = np.array([[A11    ,  A12 ,   A13 ,  0., 0., 0.],  # [vx]
            #                 [0    ,  A22 ,   A23  ,  0., 0., 0.],  # [vy]
            #                 [0    ,   A32 ,    A33  ,  0., 0., 0.],  # [wz]
            #                 [-A1*cur    ,  A1*A2*cur ,   1. ,   0., 0, 0. ],  # [epsi]
            #                 [A51    ,  A52 ,   0. ,  0., 0., 0.],  # [s]
            #                 [0.    ,  1 ,   0. ,  0., 0., A4 ]]) # [ey]


            # Ai = np.array([[A11    ,  A12 ,   A13 ,  0., 0.],  # [vx]
            #                 [0    ,  A22 ,   A23  ,  0., 0.],  # [vy]
            #                 [0    ,   A32 ,    A33  ,  0., 0.],  # [wz]
            #                 [A61    ,  A62 ,   0. ,  0., 0., ], # [ey]
            #                 [A41   ,  A42 ,   1. ,   0., 0, ]])  # [epsi]


            # print "Ai", Ai

            Ci  = np.array([[ 0 ],
                            [ 0 ],
                            [ 0 ],
                            [ 0 ],
                            [ 0 ]])


            Ai = np.eye(len(Ai)) + self.dt * Ai
            Bi = self.dt * Bi
            Ci = self.dt * Ci

            states_new = np.dot(Ai, states) + np.dot(Bi, np.transpose(np.reshape(u[i,:],(1,2))))


            STATES_vec[i] = np.reshape(states_new, (5,))

            states = states_new

            Atv.append(Ai)
            Btv.append(Bi)
            Ctv.append(Ci)


        return np.array(Atv), np.array(Btv), np.array(Ctv)




    def LPVPrediction_setup(self):
        '''
        Used for QP initial setup the element is set to '1' for possible position where value will change during the optimization 
        '''
        #############################################
        ## States:
        ##   long velocity    [vx]
        ##   lateral velocity [vy]
        ##   angular velocity [wz]
        ##   theta error      [epsi]
        ##   distance traveled[s]
        ##   lateral error    [ey]
        ##
        ## Control actions:
        ##   Steering angle   [delta]
        ##   Acceleration     [a]
        ##
        ## Scheduling variables:
        ##   vx, vy, epsi, ey, cur
        #############################################


        STATES_vec = np.zeros((self.N, self.nx))

        Atv = []
        Btv = []
        Ctv = []

        u = np.array([1, 1]).T
        states = np.array([1,1,1,1,1]).T
        for i in range(0, self.N):

            # Ai = np.array([ [1.   ,  1. ,  1. ,  0., 0. ],   # [vx]
            #                 [ 0     ,  1. ,  1.  , 0., 0. ],   # [vy]
            #                 [ 0     ,  1. ,  1.  , 0., 0. ],   # [wz]
            #                 [1.    ,  1. ,   1. ,   0., 0 ],   # [ey]
            #                 [1.    ,  1. ,   0. ,  0., 0. ]])  # [epsi] 


            #works with A1*A2*curv
            Ai = np.array([ [1.0    ,  1.0 ,  1.0  ,  0., 0.],  # [vx]
                            [0.     ,  1.0 ,  1.0  ,  0., 0.],  # [vy]
                            [0.     ,  1.0 ,  1.0  ,  0., 0.],  # [wz]
                            [0.     ,  1.0 ,   0.  ,  0., 1.],  # [ey]
                            [1.0    ,  1.0 ,   1.  ,  0., 0.]])  # [epsi]]) # [ey]




            # Ai = np.array([ [1.0    ,  1.0 ,  1.0  ,  0., 0.],  # [vx]
            #                 [1.     ,  1.0 ,  1.0  ,  0., 0.],  # [vy]
            #                 [1.     ,  1.0 ,  1.0  ,  0., 0.],  # [wz]
            #                 [1.     ,  1.0 ,   1.  ,  0., 1.],  # [ey]
            #                 [1.0    ,  1.0 ,   1.  ,  0., 0.]])  # [epsi]]) # [ey]

            Bi  = np.array([[ 1.0, 1.0 ], #[delta, a]
                            [ 1.0,  0  ],
                            [ 1.0,  0  ],
                            [  0 ,  0  ],
                            [  0 ,  0  ]])

            Ci  = np.array([[ 0 ],
                            [ 0 ],
                            [ 0 ],
                            [ 0 ],
                            [ 0 ]])


            Ai = np.eye(len(Ai)) + self.dt * Ai
            Bi = self.dt * Bi
            Ci = self.dt * Ci

            # states_new = np.dot(Ai, states) + np.dot(Bi,u)

            # STATES_vec[i] = np.reshape(states_new, (self.nx,))

            # states = states_new

            Atv.append(Ai)
            Btv.append(Bi)
            Ctv.append(Ci)


        # return STATES_vec, np.array(Atv), np.array(Btv), np.array(Ctv)
        return np.array(Atv), np.array(Btv), np.array(Ctv)


def Curvature(s, PointAndTangent):
    """curvature and desired velocity computation
    s: curvilinear abscissa at which the curvature has to be evaluated
    PointAndTangent: points and tangent vectors defining the map (these quantities are initialized in the map object)
    """
    TrackLength = PointAndTangent[-1,3]+PointAndTangent[-1,4]

    # In case on a lap after the first one
    while (s > TrackLength):
        s = s - TrackLength
    index = np.all([[s >= PointAndTangent[:, 3]], [s < PointAndTangent[:, 3] + PointAndTangent[:, 4]]], axis=0)

    i = int(np.where(np.squeeze(index))[0]) #EA: this works
    #i = np.where(np.squeeze(index))[0]     #EA: this does not work
    curvature = PointAndTangent[i, 5]

    return curvature