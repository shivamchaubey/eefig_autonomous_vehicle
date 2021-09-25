#!/usr/bin/env python

from math import tan, atan, cos, sin, pi, atan2, fmod
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.patches as patches
import rospy
from numpy.random import randn,rand
import rosbag
from geometry_msgs.msg import Twist, Pose, PoseStamped
from std_msgs.msg import Bool, Float32
from sensor_fusion.msg import sensorReading, control, hedge_imu_fusion, hedge_imu_raw
import tf
import time
from numpy import linalg as LA
import datetime
import os
import sys
import scipy.io as sio
    

################## LQR GAIN PATH ####################
gain_path = rospy.get_param("switching_lqr_observer/lqr_gain_path")
gain_path = ('/').join(sys.path[0].split('/')[:-1]) + gain_path
print "\n LQR gain path ={} \n".format(gain_path)



class lidar_pose():
    """ Object collecting hector slam pose data
    """
    def __init__(self):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """
        print "waiting for lidar message"
        rospy.wait_for_message('/slam_out_pose', PoseStamped)
        rospy.Subscriber("/slam_out_pose", PoseStamped, self.pose_callback, queue_size=1)
        sim_on  = rospy.get_param("switching_lqr_observer/sim_on")
        # ECU measurement
        self.X = 0.0
        self.Y = 0.0
        self.X_MA = 0.0
        self.Y_MA = 0.0
        self.yaw = 0.0

        if sim_on == True:
            self.offset_X = 0.0
        else:
            self.offset_X = 0.13

        self.N = 20
        self.X_MA_window = [0.0]*self.N
        self.Y_MA_window = [0.0]*self.N

    def pose_callback(self,msg):
        """Unpack message from lidar sensor"""
        
        self.X = -msg.pose.position.x + self.offset_X
        self.Y = -msg.pose.position.y
        quat = msg.pose.orientation
        euler = tf.transformations.euler_from_quaternion([quat.x, quat.y, quat.z, quat.w])
        self.yaw = euler[2]
        
        self.X_MA_window.pop(0)
        self.X_MA_window.append(self.X)
        self.X_MA = np.squeeze(np.convolve(self.X_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.Y_MA_window.pop(0)
        self.Y_MA_window.append(self.Y)
        self.Y_MA = np.squeeze(np.convolve(self.Y_MA_window, np.ones(self.N)/self.N, mode='valid'))


class vehicle_control(object):
    """ Object collecting CMD command data
    Attributes:
        Input command:
            1.a 2.df
        Time stamp
            1.t0  2.curr_time
    """
    def __init__(self,t0):
        """ Initialization
        Arguments:
            t0: starting measurement time
        """
        rospy.Subscriber('control/accel', Float32, self.accel_callback, queue_size=1)
        rospy.Subscriber('control/steering', Float32, self.steering_callback, queue_size=1)

        # ECU measurement
        self.duty_cycle  = 0.0 #dutycyle
        self.steer = 0.0

        # time stamp
        self.t0         = t0
        self.curr_time_dc  = rospy.get_rostime().to_sec() - self.t0
        self.curr_time_steer  = rospy.get_rostime().to_sec() - self.t0

    def accel_callback(self,data):
        """Unpack message from sensor, ECU"""
        self.curr_time_dc = rospy.get_rostime().to_sec() - self.t0
        self.duty_cycle  = data.data

    def steering_callback(self,data):
        self.curr_time_steer = rospy.get_rostime().to_sec() - self.t0
        self.steer = data.data

    def data_retrive(self, msg):

        msg.timestamp_ms_DC = self.curr_time_dc
        msg.timestamp_ms_steer = self.curr_time_steer
        msg.duty_cycle  = self.duty_cycle
        msg.steer = self.steer
        return msg

class motor_encoder():

    def __init__(self,t0,N = 10):

        rospy.Subscriber('/wheel_rpm_feedback', Float32, self.RPM_callback, queue_size=1)       

        self.curr_time    = 0.0
        self.wheel_rpm    = 0.0
        self.vx           = 0.0
        self.s            = 0.0

        self.wheel_rpm_MA_window = [0]*N #moving average
        self.wheel_rpm_MA = 0.0
        self.vx_MA        = 0.0
        self.s_MA         = 0.0
        self.wheel_radius     = 0.03*1.12178 #radius of wheel

        # time stamp
        self.t0     = t0
        self.N      = N
        # Time for yawDot integration
        self.curr_time = rospy.get_rostime().to_sec() - self.t0
        self.prev_time = self.curr_time

    def RPM_callback(self, data):

        self.curr_time = rospy.get_rostime().to_sec() - self.t0
        self.wheel_rpm = data.data

        self.wheel_rpm_MA_window.pop(0)
        self.wheel_rpm_MA_window.append(self.wheel_rpm)
        
        self.vx        = (self.wheel_rpm*2*pi/60.0)*self.wheel_radius
        self.s        += abs(self.vx)*(self.curr_time - self.prev_time)  

        ### Moving average
        self.wheel_rpm_MA    =  np.squeeze(np.convolve(self.wheel_rpm_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.vx_MA     = (self.wheel_rpm_MA*2*pi/60.0)*self.wheel_radius
        self.s_MA     += self.vx_MA*(self.curr_time - self.prev_time)  
        
        self.prev_time = self.curr_time

    def data_retrive(self, msg):

        msg.timestamp_ms = self.curr_time
        msg.X  = 0
        msg.Y  = 0
        msg.roll  = 0
        msg.yaw  = 0
        msg.pitch  = 0
        msg.vx  = self.vx
        msg.vy  = 0
        msg.yaw_rate  = 0
        msg.ax  = 0
        msg.ay  = 0
        msg.s  = self.s
        msg.x  = 0
        msg.y  = 0

        return msg

    def data_retrive_MA(self, msg):

        msg.timestamp_ms = self.curr_time
        msg.X  = 0
        msg.Y  = 0
        msg.roll  = 0
        msg.yaw  = 0
        msg.pitch  = 0
        msg.vx  = self.vx_MA
        msg.vy  = 0
        msg.yaw_rate  = 0
        msg.ax  = 0
        msg.ay  = 0
        msg.s  = self.s_MA
        msg.x  = 0
        msg.y  = 0

        return msg



class IMU():

    def __init__(self,t0, N):

        print "Waiting for IMU message"
        rospy.wait_for_message('pose', Pose)


        rospy.Subscriber('twist', Twist, self.Twist_callback, queue_size=1)

        rospy.Subscriber('pose', Pose, self.Pose_callback, queue_size=1)

        self.roll               = 0.0
        self.pitch              = 0.0
        self.yaw                = 0.0

        self.roll_MA            = 0.0
        self.roll_MA_window     = [0.0]*N
        self.pitch_MA           = 0.0
        self.pitch_MA_window    = [0.0]*N
        self.yaw_MA             = 0.0
        self.yaw_MA_window      = [0.0]*N

        self.roll_rate              = 0.0
        self.roll_rate_MA           = 0.0
        self.roll_rate_MA_window    = [0.0]*N

        self.pitch_rate             = 0.0
        self.pitch_rate_MA          = 0.0
        self.pitch_rate_MA_window   = [0.0]*N

        self.yaw_rate               = 0.0
        self.yaw_rate_MA            = 0.0
        self.yaw_rate_MA_window     = [0.0]*N
        self.yaw_rate_offset        = 0.0
        
        self.vx      = 0.0
        self.vy      = 0.0

        self.vx_MA              = 0.0
        self.vy_MA              = 0.0
        self.psiDot_MA          = 0.0
        
        self.ax             = 0.0
        self.ax_MA          = 0.0
        self.ax_MA_window   = [0.0]*N
        self.ay             = 0.0
        self.ay_MA          = 0.0
        self.ay_MA_window   = [0.0]*N
        self.az             = 0.0
        self.az_MA          = 0.0
        self.az_MA_window   = [0.0]*N
        
        self.X              = 0.0
        self.X_MA           = 0.0

        self.Y              = 0.0
        self.Y_MA           = 0.0
        
        self.N      = N
        
        
        ### For dead reckoning correction                        
        self.encoder = motor_encoder(t0)

        self.yaw_offset     = 0.0
        self.psiDot_offset  = 0.0
        self.ax_offset      = 0.0
        self.ay_offset      = 0.0
        self.az_offset      = 0.0
        self.vx_offset      = 0.0
        self.vy_offset      = 0.0

        self.co_yaw     = 0.0
        self.co_psiDot  = 0.0
        self.co_ax      = 0.0
        self.co_ay      = 0.0
        self.co_az      = 0.0
        self.s          = 0.0 
        self.co_vx      = 0.0
        self.co_vy      = 0.0

        # time stamp
        # self.t0     = time.time()
        self.t0     = t0

        print  "yaw_offset", self.yaw_offset


        # Time for yawDot integration
        # self.curr_time_pose = self.t0
        # self.curr_time_twist = self.t0
        
        self.curr_time_pose = rospy.get_rostime().to_sec() - self.t0
        self.curr_time_twist = rospy.get_rostime().to_sec() - self.t0
        
        self.prev_time_pose = self.curr_time_pose
        self.prev_time_twist = self.curr_time_twist


    def gravity_compensate(self):
        g = [0.0, 0.0, 0.0]
        q = [self.qx , self.qy, self.qz, self.qz]
        acc = [self.ax, self.ay, self.az]
        # get expected direction of gravity
        g[0] = 2 * (q[1] * q[3] - q[0] * q[2])
        g[1] = 2 * (q[0] * q[1] + q[2] * q[3])
        g[2] = q[0] * q[0] - q[1] * q[1] - q[2] * q[2] + q[3] * q[3]
        self.ax, self.ay, self.az =  [acc[0] + g[0], acc[1] + g[1], acc[2] + g[2]]

        

        ############ Body frame to world frame transformation ##########
        # http://planning.cs.uiuc.edu/node102.html
        self.R_ypr = np.array([[cos(self.yaw)*cos(self.pitch) , cos(self.yaw)*sin(self.pitch)*sin(self.roll) - \
                                sin(self.yaw)*cos(self.roll) , cos(self.yaw)*sin(self.pitch)*cos(self.roll) + \
                                sin(self.yaw)*sin(self.roll)], \
                                [sin(self.yaw)*cos(self.pitch) , sin(self.yaw)*sin(self.pitch)*sin(self.roll) + \
                                cos(self.yaw)*cos(self.roll) , sin(self.yaw)*sin(self.pitch)*cos(self.roll) - \
                                cos(self.yaw)*sin(self.roll)], \
                                [-sin(self.pitch) , cos(self.pitch)*sin(self.roll) , cos(self.pitch)*cos(self.roll)]])
        
        ### gravity compensation
        # http://www.varesano.net/blog/fabio/simple-gravity-compensation-9-dom-imus
        gravity = np.dot(self.R_ypr.T,np.array([0, 0, self.az]).T)
        self.ax = self.ax + gravity[0]
        self.ay = self.ay + gravity[1]
        self.az = self.az #- gravity[2]




    def Twist_callback(self, data):
        
        self.curr_time_twist = rospy.get_rostime().to_sec() - self.t0
        # self.curr_time_twist = time.time()

        # self.ax     = -1*data.linear.y - self.ax_offset
        # self.ay     = data.linear.x - self.ay_offset
        # self.az     = data.linear.z #- self.az_offset

        self.ax     = data.linear.x*0.001 #- self.ax_offset
        self.ay     = -data.linear.y*0.001 #- self.ay_offset
        self.az     = -data.linear.z*0.001 #- self.az_offset

        self.ax_MA_window.pop(0)
        self.ax_MA_window.append(self.ax)
        self.ax_MA = np.squeeze(np.convolve(self.ax_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.ay_MA_window.pop(0)
        self.ay_MA_window.append(self.ay)
        self.ay_MA = np.squeeze(np.convolve(self.ay_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.az_MA_window.pop(0)
        self.az_MA_window.append(self.az)
        self.az_MA = np.squeeze(np.convolve(self.az_MA_window, np.ones(self.N)/self.N, mode='valid'))


        self.transform = False
        if self.transform == True:
            self.coordinate_transform()


        #### Dead RECOKNING ####        
        if self.encoder.vx == 0.0:
            self.ax = 0.0
            self.ay = 0.0
            self.ax_MA = 0.0
            self.ay_sMA = 0.0
            self.vx = 0.0
            self.vy = 0.0
            self.vx_MA = 0.0
            self.vy_MA = 0.0

        # print (self.curr_time_twist,self.prev_time_twist, self.curr_time_twist-self.prev_time_twist)
        self.vx     = self.vx+self.ax*(self.curr_time_twist-self.prev_time_twist)  # from IMU
        self.vy     = self.vy+self.ay*(self.curr_time_twist-self.prev_time_twist)

        self.X = self.X +  self.vx*(self.curr_time_twist-self.prev_time_twist)
        self.Y = self.Y +  self.vy*(self.curr_time_twist-self.prev_time_twist)
        
        self.vx_MA     = self.vx_MA+self.ax_MA*(self.curr_time_twist-self.prev_time_twist)  # from IMU
        self.vy_MA     = self.vy_MA+self.ay_MA*(self.curr_time_twist-self.prev_time_twist)

        self.X_MA = self.X_MA +  self.vx_MA*(self.curr_time_twist-self.prev_time_twist)
        self.Y_MA = self.Y_MA +  self.vy_MA*(self.curr_time_twist-self.prev_time_twist)


        # self.psiDot = data.angular.z 

        # p is called roll rate, q pitch rate and r yaw rate.
        self.roll_rate    = data.angular.x   
        self.pitch_rate   = data.angular.y   
        self.yaw_rate     = -(data.angular.z  + self.yaw_rate_offset)
        
        self.roll_rate_MA_window.pop(0)
        self.roll_rate_MA_window.append(self.roll_rate)
        self.roll_rate_MA = np.squeeze(np.convolve(self.roll_rate_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.pitch_rate_MA_window.pop(0)
        self.pitch_rate_MA_window.append(self.pitch_rate)
        self.pitch_rate_MA = np.squeeze(np.convolve(self.pitch_rate_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.yaw_rate_MA_window.pop(0)
        self.yaw_rate_MA_window.append(self.yaw_rate)
        self.yaw_rate_MA = np.squeeze(np.convolve(self.yaw_rate_MA_window, np.ones(self.N)/self.N, mode='valid'))



        self.prev_time_twist = self.curr_time_twist

    def Pose_callback(self, data):
        self.curr_time_pose = rospy.get_rostime().to_sec() - self.t0
        # self.curr_time_pose = time.time()


        self.roll   = data.orientation.x 
        self.pitch  = data.orientation.y 

        # print "data.orientation.z", data.orientation.z, "yaw_offset", self.yaw_offset

        self.yaw    = wrap(data.orientation.z - (self.yaw_offset))

        self.roll_MA_window.pop(0)
        self.roll_MA_window.append(self.roll)
        self.roll_MA  = np.squeeze(np.convolve(self.roll_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.pitch_MA_window.pop(0)
        self.pitch_MA_window.append(self.pitch)
        self.pitch_MA = np.squeeze(np.convolve(self.pitch_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.yaw_MA_window.pop(0) 
        self.yaw_MA_window.append(self.yaw)
        self.yaw_MA   = np.squeeze(np.convolve(self.yaw_MA_window, np.ones(self.N)/self.N, mode='valid'))


        self.prev_time_pose = self.curr_time_pose
        

    def calibrate_imu(self,delay,offset):


        yaw_rate_info   = []
        ax_info = []
        ay_info = []
        az_info = []
        vx_info = []
        vy_info = []
        yaw_info    = []

        # t1 = rospy.get_rostime().to_sec()
        # while   t1 - self.t0 < delay: ### time for 5sec
        for i in range(50):
            # t1 = rospy.get_rostime().to_sec()
            yaw_rate_info.append(self.yaw_rate)
            ax_info.append(self.ax)
            ay_info.append(self.ay)
            az_info.append(self.az)
            
            vx_info.append(self.vx)
            vy_info.append(self.vy)
            yaw_info.append(self.yaw)
            # print ('time', t1 - self.t0)        

        self.yaw_rate_offset  = np.mean(yaw_rate_info)
        self.ax_offset      = np.mean(ax_info)
        self.ay_offset      = np.mean(ay_info)
        self.az_offset      = np.mean(az_info)
        self.vx_offset      = np.mean(vx_info)
        self.vy_offset      = np.mean(vy_info)
        self.yaw_offset     = np.mean(yaw_info)  - offset

        self.co_yaw     = np.var(yaw_info)
        self.co_psiDot  = np.var(yaw_rate_info)
        self.co_ax      = np.var(ax_info)
        self.co_ay      = np.var(ay_info)
        self.co_vx      = np.var(vx_info)
        self.co_vy      = np.var(vy_info)



    def data_retrive(self, msg):

        msg.timestamp_ms = self.curr_time_pose
        msg.X  = 0
        msg.Y  = 0
        msg.roll  = self.roll
        msg.yaw  = self.yaw
        msg.pitch  = self.pitch
        msg.vx  = self.vx
        msg.vy  = self.vy
        msg.yaw_rate  = self.yaw_rate
        msg.ax  = self.ax
        msg.ay  = self.ay
        msg.s  = self.s
        msg.x  = self.X
        msg.y  = self.Y

        return msg

    def data_retrive_MA(self, msg):

        msg.timestamp_ms = self.curr_time_pose
        msg.X  = 0
        msg.Y  = 0
        msg.roll  = self.roll_MA
        msg.yaw  = self.yaw_MA
        msg.pitch  = self.pitch_MA
        msg.vx  = self.vx_MA
        msg.vy  = self.vy_MA
        msg.yaw_rate  = self.yaw_rate_MA
        msg.ax  = self.ax_MA
        msg.ay  = self.ay_MA
        msg.s  = self.s
        msg.x  = self.X_MA
        msg.y  = self.Y_MA

        return msg

class fiseye_cam():


    def __init__(self,t0,N = 10):

        print "waiting for camera message"
        rospy.wait_for_message('fused_cam_pose', Pose)

        # rospy.Subscriber('pure_cam_pose', Pose, self.pure_cam_pose_callback, queue_size=1)
        

        rospy.Subscriber('fused_cam_pose', Pose, self.fused_cam_pose_callback, queue_size=1)


        #### Homogeneous transformation for reference change####
        self.x_tf     = rospy.get_param("switching_lqr_observer/x_tf")
        self.y_tf     = rospy.get_param("switching_lqr_observer/y_tf")
        theta_tf = rospy.get_param("switching_lqr_observer/theta_tf")*pi/180
        self.R_tf = np.array([[cos(theta_tf), -sin(theta_tf)],
                         [sin(theta_tf),  cos(theta_tf)]])
        self.yaw_tf   = rospy.get_param("switching_lqr_observer/yaw_tf")*pi/180

        self.x_m_offset         = 0 
        self.y_m_offset         = 0 

        self.N_v                = 50

        self.vx                 = 0.0
        self.vx_prev            = 0.0
        self.vx_MA_window       = [0.0]*self.N_v
        self.vx_MA              = 0.0

        self.yaw = 0.0
        # self.vx_MA_window    = [0.0]*N
        # self.vx_MA               = 0.0

        self.vy                 = 0.0
        self.vy_prev            = 0.0
        self.vy_MA_window       = [0.0]*self.N_v
        self.vy_MA              = 0.0


        self.ax                 = 0.0
        self.ay                 = 0.0
        
        # time stamp
        self.t0     = t0
        self.N      = N

        self.encoder = motor_encoder(t0)
        # self.vy_MA_window    = [0.0]*N
        # self.vy_MA               = 0.0


        self.X                 = 0.0
        self.X_MA_window       = [0.0]*N
        self.X_MA              = 0.0
        self.X_MA_past         = 0.0

        self.Y                 = 0.0
        self.Y_MA_window       = [0.0]*N
        self.Y_MA              = 0.0
        self.Y_MA_past         = 0.0

        self.s = 0.0
        
        self.yaw = 0.0
        self.yaw_MA = 0.0
        self.yaw_MA_window = [0.0]*N


        # Time for yawDot integration
        self.curr_time = rospy.get_rostime().to_sec() - self.t0
        self.prev_time = self.curr_time

        #         #history
        # self.twist_hist = {"timestamp_ms":[],"vx":[],"vy":[],"psiDot":[],"ax":[],"ay":[],"az":[]}
        # self.pose_hist = {"timestamp_ms":[],"roll":[],"pitch":[],"yaw":[]}
        # self.wheel_rpm_hist = {"timestamp_ms":[],"wheel_rpm":[]}

    def pure_cam_pose_callback(self, data):
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        self.pure_x   = data.position.x
        self.pure_y   = data.position.y
        self.pure_yaw = data.orientation.z


    def fused_cam_pose_callback(self, data):
        self.curr_time = rospy.get_rostime().to_sec() - self.t0

        # self.X   = data.position.x - self.x_m_offset
        # self.Y   = data.position.y - self.y_m_offset

        [self.X, self.Y] = np.dot(self.R_tf, np.array([data.position.x,data.position.y]).T)
        self.X = self.X - self.x_tf
        self.Y = self.Y - self.y_tf
        # print "data.orientation.z",data.orientation.z,"self.yaw_tf", self.yaw_tf
        self.yaw = wrap(data.orientation.z + self.yaw_tf)

        self.X_MA_window.pop(0)
        self.X_MA_window.append(self.X)
        self.X_MA = np.squeeze(np.convolve(self.X_MA_window, np.ones(self.N)/self.N, mode='valid'))
        

        self.Y_MA_window.pop(0)
        self.Y_MA_window.append(self.Y)
        self.Y_MA = np.squeeze(np.convolve(self.Y_MA_window, np.ones(self.N)/self.N, mode='valid'))

        self.yaw_MA_window.pop(0)
        self.yaw_MA_window.append(self.yaw)
        self.yaw_MA = np.squeeze(np.convolve(self.yaw_MA_window, np.ones(self.N)/self.N, mode='valid'))


        #### Position and velocity calculation ####

        ### Velocity and orientation in inertial frame (dx/dt)
        Gvx = (self.X_MA_window[-1]- self.X_MA_window[-2])/(self.curr_time -self.prev_time)

        Gvy = (self.Y_MA_window[-1]- self.Y_MA_window[-2])/(self.curr_time -self.prev_time)

        Gvx_MA = (self.X_MA - self.X_MA_past)/(self.curr_time -self.prev_time)

        Gvy_MA = (self.Y_MA - self.Y_MA_past)/(self.curr_time -self.prev_time)

        dist =  LA.norm(np.array([self.X_MA_window[-1],self.Y_MA_window[-1]])-np.array([self.X_MA_window[-2], self.Y_MA_window[-2]]))

        if self.encoder.vx == 0.0:
            Gvx = 0.0
            Gvy = 0.0        
            Gvx_MA = 0.0
            Gvy_MA = 0.0        
            
            dist = 0.0


        ### velocity in vehicle body frame
        # Vx = (dist - self.s)/(self.curr_time -self.prev_time)
        # Vy = (Gvx*cos(self.yaw)+Gvy*sin(self.yaw))

        Vx_MA = (Gvx_MA*cos(self.yaw_MA)+Gvy_MA*sin(self.yaw_MA))
        Vy_MA = (-Gvx_MA*sin(self.yaw_MA)+Gvy_MA*cos(self.yaw_MA))

        Vx = (Gvx*cos(self.yaw)+Gvy*sin(self.yaw))
        Vy = (-Gvx*sin(self.yaw)+Gvy*cos(self.yaw))


        self.s += dist

        
        ### Filter large values which is not possible ###

        # if Vx > 0.1:
        #     Vx = self.vx
        #     Vy = self.vy

        self.vx = Vx
        self.vy = Vy

        self.vx_MA_window.pop(0)
        self.vx_MA_window.append(Vx_MA)
        self.vx_MA = np.squeeze(np.convolve(self.vx_MA_window, np.ones(self.N_v)/self.N_v, mode='valid'))

        self.vy_MA_window.pop(0)
        self.vy_MA_window.append(Vy_MA)
        self.vy_MA = np.squeeze(np.convolve(self.vy_MA_window, np.ones(self.N_v)/self.N_v, mode='valid'))

        ### acceleration in vehicle body frame
        self.ax = (self.vx-self.vx_prev)/(self.curr_time -self.prev_time)
        self.ay = (self.vy-self.vy_prev)/(self.curr_time -self.prev_time)

        self.vy_prev = self.vy

        # print (np.array([self.X_MA_window[-1],self.Y_MA_window[-1]])-np.array([self.X_MA_window[-2], self.Y_MA_window[-2]]))


        self.prev_time = self.curr_time
        self.X_MA_past = self.X_MA
        self.Y_MA_past = self.Y_MA

    def calibrate_fcam(self,delay,offset):

        x_m_info = []
        y_m_info = []

        for i in range(100):
            x_m_info.append(self.X)
            y_m_info.append(self.Y)
        

        self.x_m_offset = np.mean(x_m_info)
        self.y_m_offset = np.mean(y_m_info)


    def data_retrive(self, msg):

        msg.timestamp_ms = self.curr_time
        msg.X  = self.X
        msg.Y  = self.Y
        msg.roll  = 0
        msg.yaw  = self.yaw
        msg.pitch  = 0
        msg.vx  = self.vx
        msg.vy  = self.vy
        msg.yaw_rate  = 0.0
        msg.ax  = self.ax
        msg.ay  = self.ay
        msg.s  = self.s
        msg.x  = 0.0
        msg.y  = 0.0

        return msg

    def data_retrive_MA(self, msg):
        msg.timestamp_ms = self.curr_time
        msg.X  = self.X_MA
        msg.Y  = self.Y_MA
        msg.roll  = 0
        msg.yaw  = self.yaw_MA
        msg.pitch  = 0
        msg.vx  = self.vx_MA
        msg.vy  = self.vy_MA
        msg.yaw_rate  = 0.0
        msg.ax  = self.ax
        msg.ay  = self.ay
        msg.s  = self.s
        msg.x  = 0.0
        msg.y  = 0.0

        return msg


def _initializeFigure_xy(x_lim,y_lim):

    xdata = []; ydata = []
    fig = plt.figure(figsize=(10,8))
    plt.ion()
    plt.xlim([-1*x_lim,x_lim])
    plt.ylim([-1*y_lim,y_lim])

    axtr = plt.axes()

    line_ol,        = axtr.plot(xdata, ydata, '-k', label = 'Open loop simulation')
    line_est,    = axtr.plot(xdata, ydata, '-r', label = 'Estimated states')  # Plots the traveled positions
    line_meas,    = axtr.plot(xdata, ydata, '-b', label = 'Measured position camera')  # Plots the traveled positions
    # line_tr,        = axtr.plot(xdata, ydata, '-r', linewidth = 6, alpha = 0.5)       # Plots the current positions
    # line_SS,        = axtr.plot(xdata, ydata, '-g', , linewidth = 10, alpha = 0.5)
    # line_pred,      = axtr.plot(xdata, ydata, '-or')
    # line_planning,  = axtr.plot(xdata, ydata, '-ok')
    
    l = 0.4; w = 0.2 #legth and width of the car

    v = np.array([[ 1,  1],
                  [ 1, -1],
                  [-1, -1],
                  [-1,  1]])

    # Estimated states:
    rec_est = patches.Polygon(v, alpha=0.7, closed=True, fc='r', ec='k', zorder=10)
    axtr.add_patch(rec_est)

    # Open loop simulation:
    rec_ol = patches.Polygon(v, alpha=0.7, closed=True, fc='k', ec='k', zorder=10)
    axtr.add_patch(rec_ol)

    # Open loop simulation:
    rec_meas = patches.Polygon(v, alpha=0.7, closed=True, fc='b', ec='k', zorder=10)
    axtr.add_patch(rec_meas)


    plt.legend()
    return fig, axtr, line_est, line_ol, line_meas, rec_est, rec_ol, rec_meas




def getCarPosition(x, y, psi, w, l):
    car_x = [ x + l * np.cos(psi) - w * np.sin(psi), x + l * np.cos(psi) + w * np.sin(psi),
              x - l * np.cos(psi) + w * np.sin(psi), x - l * np.cos(psi) - w * np.sin(psi)]
    car_y = [ y + l * np.sin(psi) + w * np.cos(psi), y + l * np.sin(psi) - w * np.cos(psi),
              y - l * np.sin(psi) - w * np.cos(psi), y - l * np.sin(psi) + w * np.cos(psi)]
    return car_x, car_y

def append_sensor_data(data,msg):
    data['timestamp_ms'].append(msg.timestamp_ms)
    data['X'].append(msg.X)
    data['Y'].append(msg.Y)
    data['roll'].append(msg.roll)
    data['yaw'].append(msg.yaw)
    data['pitch'].append(msg.pitch)
    data['vx'].append(msg.vx)
    data['vy'].append(msg.vy)
    data['yaw_rate'].append(msg.yaw_rate)
    data['ax'].append(msg.ax)
    data['ay'].append(msg.ay)
    data['s'].append(msg.s)
    data['x'].append(msg.x)
    data['y'].append(msg.y)

def append_control_data(data,msg):
   
    data['timestamp_ms_dutycycle'].append(msg.timestamp_ms_DC)
    data['timestamp_ms_steer'].append(msg.timestamp_ms_steer)
    data['steering'].append(msg.steer)
    data['duty_cycle'].append(msg.duty_cycle)


def Continuous_AB_Comp_old(vx, vy, omega, theta, delta):


#     %%% Parameters
    m = rospy.get_param("m")
    rho = rospy.get_param("rho")
    lr = rospy.get_param("lr")
    lf = rospy.get_param("lf")
    Cm0 = rospy.get_param("Cm0")
    Cm1 = rospy.get_param("Cm1")
    C0 = rospy.get_param("C0")
    C1 = rospy.get_param("C1")
    Cd_A = rospy.get_param("Cd_A")
    Caf = rospy.get_param("Caf")
    Car = rospy.get_param("Car")
    Iz = rospy.get_param("Iz")

    F_flat = 0;
    Fry = 0;
    Frx = 0;
    
    A31 = 0;
    A11 = 0;
    
    eps = 0.0000001
    # eps = 0
    # # if abs(vx)> 0:
    # F_flat = 2*Caf*(delta- atan((vy+lf*omega)/(vx+eps)));
    # Fry = -2*Car*atan((vy - lr*omega)/(vx+eps)) ;

    
    F_flat = 2*Caf*(delta- atan((vy+lf*omega)/(vx+eps)));
    Fry = -2*Car*atan((vy - lr*omega)/(vx+eps)) ;

    A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
    A31 = -Fry*lr/((vx+eps)*Iz);
        
    A12 = omega;
    A21 = -omega;
    A22 = 0;
    
    # if abs(vy) > 0.0:
    A22 = Fry/(m*(vy+eps));

    A41 = cos(theta);
    A42 = -sin(theta);
    A51 = sin(theta);
    A52 = cos(theta);


    B12 = 0;
    B32 = 0;
    B22 = 0;
    

    B12 = -F_flat*sin(delta)/(m*(delta+eps));
    B22 = F_flat*cos(delta)/(m*(delta+eps));    
    B32 = F_flat*cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0,  0,   0,  0],\
                  [A21, A22, 0,  0,   0,  0],\
                  [A31,  0 , 0,  0,   0,  0],\
                  [A41, A42, 0,  0,   0,  0],\
                  [A51, A52, 0,  0,   0,  0],\
                  [ 0 ,  0 , 1,  0,   0,  0]])
    
    # print "A = {}".format(A), "Det A = {}".format(LA.det(A))

    B = np.array([[B11, B12],\
                  [ 0,  B22],\
                  [ 0,  B32],\
                  [ 0 ,  0 ],\
                  [ 0 ,  0 ],\
                  [ 0 ,  0 ]])
        
    # print "B = {}".format(B), "Det B = {}".format(LA.det(B))

    return A, B


def Continuous_AB_Comp_old(vx, vy, omega, theta, delta):


#     %%% Parameters
    m = rospy.get_param("m")
    rho = rospy.get_param("rho")
    lr = rospy.get_param("lr")
    lf = rospy.get_param("lf")
    Cm0 = rospy.get_param("Cm0")
    Cm1 = rospy.get_param("Cm1")
    C0 = rospy.get_param("C0")
    C1 = rospy.get_param("C1")
    Cd_A = rospy.get_param("Cd_A")
    Caf = rospy.get_param("Caf")
    Car = rospy.get_param("Car")
    Iz = rospy.get_param("Iz")

    F_flat = 0;
    Fry = 0;
    Frx = 0;
    
    A31 = 0;
    A11 = 0;
    
    eps = 0.0000001
    # eps = 0
    # if abs(vx)> 0:
    F_flat = 2*Caf*(delta- atan((vy+lf*omega)/(vx+eps)));
    Fry = -2*Car*atan((vy - lr*omega)/(vx+eps)) ;
    A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
    A31 = -Fry*lr/((vx+eps)*Iz);
        
    A12 = omega;
    A21 = -omega;
    A22 = 0;
    
    # if abs(vy) > 0.0:
    A22 = Fry/(m*(vy+eps));

    A41 = cos(theta);
    A42 = -sin(theta);
    A51 = sin(theta);
    A52 = cos(theta);


    B12 = 0;
    B32 = 0;
    B22 = 0;
    

    B12 = -F_flat*sin(delta)/(m*(delta+eps));
    B22 = F_flat*cos(delta)/(m*(delta+eps));    
    B32 = F_flat*cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0,  0,   0,  0],\
                  [A21, A22, 0,  0,   0,  0],\
                  [A31,  0 , 0,  0,   0,  0],\
                  [A41, A42, 0,  0,   0,  0],\
                  [A51, A52, 0,  0,   0,  0],\
                  [ 0 ,  0 , 1,  0,   0,  0]])
    
    # print "A = {}".format(A), "Det A = {}".format(LA.det(A))

    B = np.array([[B11, B12],\
                  [ 0,  B22],\
                  [ 0,  B32],\
                  [ 0 ,  0 ],\
                  [ 0 ,  0 ],\
                  [ 0 ,  0 ]])
        
    # print "B = {}".format(B), "Det B = {}".format(LA.det(B))

    return A, B



def  LPV_model(states,u):


    vx = states[0]
    vy = states[1]
    omega = states[2]
    theta = states[5]

    delta = u[1]


    #%% Parameters
    # m = 2.424; # for 5000mAh battery

    # m = rospy.get_param("m")
    # rho = rospy.get_param("rho")
    # lr = rospy.get_param("lr")
    # lf = rospy.get_param("lf")
    # Cm0 = rospy.get_param("Cm0")
    # Cm1 = rospy.get_param("Cm1")
    # C0 = rospy.get_param("C0")
    # C1 = rospy.get_param("C1")
    # Cd_A = rospy.get_param("Cd_A")
    # Caf = rospy.get_param("Caf")
    # Car = rospy.get_param("Car")
    # Iz = rospy.get_param("Iz")





#     p = np.array([2.07583775e-30, 1.48835031e-02, 9.49681836e-05, 2.75388659e+00,
#        1.27103552e-02, 1.00000000e+02, 1.00131788e-36, 1.67942761e+01])
    
#     Cm0 = p[0]
# #     print cm0
#     Cm1 = p[1]
#     C0 = p[2]
#     C1 = p[3]
#     Cd_A = p[4]
#     Car = p[5]
#     Caf = p[6]
#     Iz = p[7]
    


    # Caf = 1.3958;
    # Car = 1.6775;   
    # Iz = 0.02;

    # Caf, Car, Iz = np.array([0.53270958, 1.27886038, 0.0101568 ])

    m = 2.483;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.1377;
    Cm0 = 10.1305;
    Cm1 = 1.05294;
    C0 = 3.68918;
    C1 = 0.0306803;
    Cd_A = -0.657645;
    Caf = 40.62927783;
    Car = 69.55846999;
    Iz = 1.01950479;

    # Caf = 1.3958;
    # Car = 1.6775;
    # Iz = 0.04

    F_flat = 0.;
    Fry = 0.;
    Frx = 0.;
    
    A31 = 0.;
    A11 = 0.;
    
    eps = 0.00
    # # eps = 0
    if abs(vx)>0.4:
        Caf = 40.62927783;
        Car = 69.55846999;
        # Iz = 1.01950479;
    else:

        Caf = 1.3958;
        Car = 1.6775;
        # Iz = 0.04

    if abs(vx)> 0.:


        F_flat = 2*Caf*(delta- np.arctan((vy+lf*omega)/abs(vx+eps)));
        Fry = -2*Car*np.arctan((vy - lr*omega)/abs(vx+eps)) ;

        A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
        A31 = -Fry*lr/((vx+eps)*Iz);
                
    A12 = omega;
    A21 = -omega;
    A22 = 0.;
    
    if abs(vy) > 0.0:
      A22 = Fry/(m*(vy+eps));

    A41 = cos(theta);
    A42 = -sin(theta);
    A51 = sin(theta);
    A52 = cos(theta);

    
    B12 = 0;
    B32 = 0;
    B22 = 0;
    
    if delta !=0.0:

        B12 = -F_flat*sin(delta)/(m*(delta+eps));
        B22 = F_flat*cos(delta)/(m*(delta+eps));    
        B32 = F_flat*cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0.,  0.,   0.,  0.],\
                  [A21, A22, 0.,  0.,   0.,  0.],\
                  [A31,  0. , 0.,  0.,   0.,  0.],\
                  [A41, A42, 0.,  0.,   0.,  0.],\
                  [A51, A52, 0.,  0.,   0.,  0.],\
                  [ 0. ,  0. , 1.,  0.,   0.,  0.]])
#     
#     print "A = {}".format(A), "Det A = {}".format(LA.det(A))

    B = np.array([[B11, B12],\
                  [ 0.,  B22],\
                  [ 0.,  B32],\
                  [ 0. ,  0. ],\
                  [ 0. ,  0. ],\
                  [ 0. ,  0. ]])
    
#     A = np.eye(len(A)) + dt * A
#     B = dt * B
    
# #     print "B = {}".format(B), "Det B = {}".format(LA.det(B))

#     states_new = np.dot(A, states) + np.dot(B, u)

    return A, B



def  LPV_model_high(states,u):


    vx = states[0]
    vy = states[1]
    omega = states[2]
    theta = states[5]

    delta = u[1]


    #%% Parameters
    # m = 2.424; # for 5000mAh battery

    # m = rospy.get_param("m")
    # rho = rospy.get_param("rho")
    # lr = rospy.get_param("lr")
    # lf = rospy.get_param("lf")
    # Cm0 = rospy.get_param("Cm0")
    # Cm1 = rospy.get_param("Cm1")
    # C0 = rospy.get_param("C0")
    # C1 = rospy.get_param("C1")
    # Cd_A = rospy.get_param("Cd_A")
    # Caf = rospy.get_param("Caf")
    # Car = rospy.get_param("Car")
    # Iz = rospy.get_param("Iz")


    m = 2.483;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.1377;
    Cm0 = 10.1305;
    Cm1 = 1.05294;
    C0 = 3.68918;
    C1 = 0.0306803;
    Cd_A = -0.657645;
    Caf = 40.62927783;
    Car = 69.55846999;
    Iz = 1.01950479;


#     p = np.array([2.07583775e-30, 1.48835031e-02, 9.49681836e-05, 2.75388659e+00,
#        1.27103552e-02, 1.00000000e+02, 1.00131788e-36, 1.67942761e+01])
    
#     Cm0 = p[0]
# #     print cm0
#     Cm1 = p[1]
#     C0 = p[2]
#     C1 = p[3]
#     Cd_A = p[4]
#     Car = p[5]
#     Caf = p[6]
#     Iz = p[7]
    


    # Caf = 1.3958;
    # Car = 1.6775;   
    # Iz = 0.02;

    # Caf, Car, Iz = np.array([0.53270958, 1.27886038, 0.0101568 ])

    F_flat = 0;
    Fry = 0;
    Frx = 0;
    
    A31 = 0;
    A11 = 0;
    
    eps = 0.00
    # eps = 0
    if abs(vx)> 0:
        F_flat = 2*Caf*(delta- np.arctan((vy+lf*omega)/abs(vx+eps)));
        Fry = -2*Car*np.arctan((vy - lr*omega)/abs(vx+eps)) ;
        A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
        A31 = -Fry*lr/((vx+eps)*Iz);
                
    A12 = omega;
    A21 = -omega;
    A22 = 0;
    
    if abs(vy) > 0.0:
      A22 = Fry/(m*(vy+eps));

    A41 = cos(theta);
    A42 = -sin(theta);
    A51 = sin(theta);
    A52 = cos(theta);

    
    B12 = 0;
    B32 = 0;
    B22 = 0;
    
    if delta !=0.0:

        B12 = -F_flat*sin(delta)/(m*(delta+eps));
        B22 = F_flat*cos(delta)/(m*(delta+eps));    
        B32 = F_flat*cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0.,  0.,   0.,  0.],\
                  [A21, A22, 0.,  0.,   0.,  0.],\
                  [A31,  0. , 0.,  0.,   0.,  0.],\
                  [A41, A42, 0.,  0.,   0.,  0.],\
                  [A51, A52, 0.,  0.,   0.,  0.],\
                  [ 0. ,  0. , 1.,  0.,   0.,  0.]])
#     
#     print "A = {}".format(A), "Det A = {}".format(LA.det(A))

    B = np.array([[B11, B12],\
                  [ 0.,  B22],\
                  [ 0.,  B32],\
                  [ 0. ,  0. ],\
                  [ 0. ,  0. ],\
                  [ 0. ,  0. ]])
    
#     A = np.eye(len(A)) + dt * A
#     B = dt * B
    
# #     print "B = {}".format(B), "Det B = {}".format(LA.det(B))

#     states_new = np.dot(A, states) + np.dot(B, u)

    return A, B



def Continuous_AB_Comp(vx, vy, omega, theta, delta):

    # m = rospy.get_param("m")
    # rho = rospy.get_param("rho")
    # lr = rospy.get_param("lr")
    # lf = rospy.get_param("lf")
    # Cm0 = rospy.get_param("Cm0")
    # Cm1 = rospy.get_param("Cm1")
    # C0 = rospy.get_param("C0")
    # C1 = rospy.get_param("C1")
    # Cd_A = rospy.get_param("Cd_A")
    # Caf = rospy.get_param("Caf")
    # Car = rospy.get_param("Car")
    # Iz = rospy.get_param("Iz")


    m = 2.424;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.132;
    # Cm0 = 11.56853;
    # Cm1 = 0.667237;
    # C0 = 2.61049;
    # C1 =  -0.00213596;
    # Cd_A = -0.466602;
    # Caf = 1.3958;
    # Car = 1.6775;   
    # Iz = 0.02;


    p = np.array([0.01003457, 0.0099731 , 0.0099466 , 0.00994527, 0.00997194, 0.01675736, 0.01012141, 0.0001786 ])

    Cm0 = p[0]
#     print cm0
    Cm1 = p[1]
    C0 = p[2]
    C1 = p[3]
    Cd_A = p[4]
    Car = p[5]
    Caf = p[6]
    Iz = p[7]


    # m = 2.424;
    # rho = 1.225;
    # lr = 0.1203;
    # lf = 0.132;
    # Cm0 = 10.1305;
    # Cm1 = 1.05294;
    # C0 = 3.68918;
    # C1 = 0.0306803;
    # Cd_A = -0.657645;
    # Caf = 1.3958;
    # Car = 1.6775;   
    # Iz = 0.04;
    # Caf = 1.3958;
    # Car = 1.6775 ;
    

    # m = 2.424;
    # rho = 1.225;
    # lr = 0.1203;
    # lf = 0.132;
    # Cm0 = 10.1305;
    # Cm1 = 1.05294;
    # C0 = 3.68918;
    # C1 = 0.0306803;
    # Cd_A = -0.657645;
    # Caf = 1.3958;
    # Car = 1.6775;   
    # Iz = 0.00578667;
    # Caf = -0.591976;
    # Car = 1.80927 ;


    F_flat = 0;
    Fry = 0.0;
    Frx = 0.0;
    A11 = 0.0;
    A31 = 0.0;
    
    # eps = 0.000001
    eps = 0.0
    if abs(vx) != 0.0:
    #     eps = 0.001

        F_flat = 2*Caf*(delta- np.arctan((vy+lf*omega)/(vx+eps)));
    
    
    
    Fry = -2*Car*np.arctan((vy - lr*omega)/(vx+eps)) ;
    A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
    A31 = -Fry*lr/((vx+eps)*Iz);
            
    A12 = omega;
    A21 = -omega;
    A22 = 0.0

    # eps = 0.0
    if abs(vy) != 0.0:
    #     eps = 0.001

        A22 = Fry/(m*(vy+eps));

    A41 = np.cos(theta);
    A42 = -np.sin(theta);
    A51 = np.sin(theta);
    A52 = np.cos(theta);

    B12 = 0.0 
    B22 = 0.0 
    B32 = 0.0 

    # eps = 0.0
    if abs(delta) != 0.0:
    #     eps = 0.001

        B12 = -F_flat*np.sin(delta)/(m*(delta+eps));
        B22 = F_flat*np.cos(delta)/(m*(delta+eps));    
        B32 = F_flat*np.cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0,  0,   0,  0],\
                  [A21, A22, 0,  0,   0,  0],\
                  [A31,  0 , 0,  0,   0,  0],\
                  [A41, A42, 0,  0,   0,  0],\
                  [A51, A52, 0,  0,   0,  0],\
                  [ 0 ,  0 , 1,  0,   0,  0]]) 
    
    B = np.array([[B11, B12],\
                    [ 0,  B22],\
                    [ 0,  B32],\
                    [ 0 ,  0 ],\
                    [ 0 ,  0 ],\
                    [ 0 ,  0 ]])

    
    # print ('self.A_obs',self.A_obs,'self.B_obs',self.B_obs)
    
    return A, B
















def Continuous_AB_Comp_old(x, u):
    
    m = 2.424;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.1377;
    
    
    vx =    x[0]
    vy =    x[1]
    omega = x[2]
    X =     x[3]
    Y =     x[4]
    theta = x[5]
    D = u[0]
    delta = u[1]
    
    p = np.array([0.01003457, 0.0099731 , 0.0099466 , 0.00994527, 0.00997194, 0.01675736, 0.01012141, 0.0001786 ])

    Cm0 = p[0]
#     print cm0
    Cm1 = p[1]
    C0 = p[2]
    C1 = p[3]
    Cd_A = p[4]
    Car = p[5]
    Caf = p[6]
    Iz = p[7]
    
    
    F_flat = 0;
    Fry = 0.0;
    Frx = 0.0;
    A11 = 0.0;
    A31 = 0.0;
    
    # eps = 0.000001
    eps = 0.0
    # if abs(vx) == 0.0:
    #     eps = 0.001

    F_flat = 2*Caf*(delta- np.arctan((vy+lf*omega)/(vx+eps)));
    
    
    
    Fry = -2*Car*np.arctan((vy - lr*omega)/(vx+eps)) ;
    A11 = -(1/m)*(C0 + C1/(vx+eps) + Cd_A*rho*vx/2);
    A31 = -Fry*lr/((vx+eps)*Iz);
            
    A12 = omega;
    A21 = -omega;
    A22 = 0.0

    # eps = 0.0
    # if abs(vy) == 0.0:
    #     eps = 0.001

    A22 = Fry/(m*(vy+eps));

    A41 = np.cos(theta);
    A42 = -np.sin(theta);
    A51 = np.sin(theta);
    A52 = np.cos(theta);

    B12 = 0.0 
    B22 = 0.0 
    B32 = 0.0 

    # eps = 0.0
    # if abs(delta) == 0.0:
    #     eps = 0.001

    B12 = -F_flat*np.sin(delta)/(m*(delta+eps));
    B22 = F_flat*np.cos(delta)/(m*(delta+eps));    
    B32 = F_flat*np.cos(delta)*lf/(Iz*(delta+eps));



    B11 = (1/m)*(Cm0 - Cm1*vx);
    
    A = np.array([[A11, A12, 0,  0,   0,  0],\
                  [A21, A22, 0,  0,   0,  0],\
                  [A31,  0 , 0,  0,   0,  0],\
                  [A41, A42, 0,  0,   0,  0],\
                  [A51, A52, 0,  0,   0,  0],\
                  [ 0 ,  0 , 1,  0,   0,  0]])
    
    B = np.array([[B11, B12],\
                    [ 0,  B22],\
                    [ 0,  B32],\
                    [ 0 ,  0 ],\
                    [ 0 ,  0 ],\
                    [ 0 ,  0 ]])

    
    return A, B




def dynamic(x,u):
    
    m = 2.424;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.1377;
    
    
    vx =    x[0]
    vy =    x[1]
    omega = x[2]
    X =     x[3]
    Y =     x[4]
    theta = x[5]
    D = u[0]
    delta = u[1]
    
    dt = 1.0/100
    eps = 0.0

    p = np.array([0.01003457, 0.0099731 , 0.0099466 , 0.00994527, 0.00997194, 0.01675736, 0.01012141, 0.0001786 ])

    cm0 = p[0]
#     print cm0
    cm1 = p[1]
    C0 = p[2]
    C1 = p[3]
    Cd_A = p[4]
    Car = p[5]
    Caf = p[6]
    Iz = p[7]
    
    
#     print "vx", vx
    Frx    = (cm0 - cm1*vx)*D - C0*vx - C1 - (Cd_A*rho*vx**2)/2;
    

    Fry = -2.0*Car*np.arctan((vy - lr*omega)/(vx+eps)) ;
    F_flat = 2.0*Caf*(delta - np.arctan((vy+lf*omega)/(vx+eps)));
    
#     print (Frx - F_flat)#, np.sin(delta)
    vx = vx + dt*(1/m)*(Frx - F_flat*np.sin(delta) + m*vy*omega);

    vy = vy * dt*(1/m)*(F_flat*np.cos(delta) + Fry - m*vx*omega);

    omega = omega + dt*(1.0/Iz)*(lf*F_flat*np.cos(delta) - lr*Fry);
        
    X = X + dt*(vx*np.cos(theta) - vy*np.sin(theta))
    Y = Y + dt*(vx*np.sin(theta) + vy*np.cos(theta))
    theta = theta + dt*omega
    
    return np.array([vx, vy, omega, X, Y, theta]).T


def Continuous_AB_Comp_old(vx, vy, omega, theta, delta):

    # m = rospy.get_param("m")
    # rho = rospy.get_param("rho")
    # lr = rospy.get_param("lr")
    # lf = rospy.get_param("lf")
    # Cm0 = rospy.get_param("Cm0")
    # Cm1 = rospy.get_param("Cm1")
    # C0 = rospy.get_param("C0")
    # C1 = rospy.get_param("C1")
    # Cd_A = rospy.get_param("Cd_A")
    # Caf = rospy.get_param("Caf")
    # Car = rospy.get_param("Car")
    # Iz = rospy.get_param("Iz")


    m = 2.424;
    rho = 1.225;
    lr = 0.1203;
    lf = 0.132;
    Cm0 = 10.1305;
    Cm1 = 1.05294;
    C0 = 3.68918;
    C1 = 0.0306803;
    Cd_A = -0.657645;
    Caf = 1.3958;
    Car = 1.6775;   
    Iz = 0.04;
    Caf = 2.0095;
    Car = 3.1816 ;
    




    A11 = 0.0
    A12 = 0.0
    A13 = 0.0
    A22 = 0.0
    A23 = 0.0
    A32 = 0.0
    A33 = 0.0
    B32 = 0.0

    eps = 0.00
    ## et to not go to nan
    if abs(vx) > 0.000001:  
        A11 = -(1/m)*(C0 + C1/(eps+vx) + Cd_A*rho*vx/2);
        A12 = 2*Caf*sin(delta)/(m*(vx+eps)) 
        A13 = 2*Caf*lf*sin(delta)/(m*(vx+eps)) + vy
        A22 = -(2*Car + 2*Caf*cos(delta))/(m*(vx+eps))
        A23 = (2*Car*lr - 2*Caf*lf*cos(delta))/(m*(vx+eps)) - vx
        A32 = (2*Car*lr - 2*Caf*lf*cos(delta))/(Iz*(vx+eps))
        A33 = -(2*Car*lf*lf*cos(delta) + 2*Caf*lr*lr)/(Iz*(vx+eps))
        B32 = 2*Caf*lf*cos(delta)/(Iz*(vx+eps))

    A41 = cos(theta);
    A42 = -sin(theta);
    A51 = sin(theta);
    A52 = cos(theta);
    B12 = -(2*Caf*sin(delta))/m
    B11 = (Cm0 - Cm1*vx)/m
    B22 = 2*Caf*cos(delta)/m


    B_obs  = np.array([[ B11, B12 ], #[duty, delta]
                        [ 0,   B22 ],
                        [ 0,   B32 ],
                        [ 0,    0  ],
                        [ 0,    0  ],
                        [ 0,    0  ]])


    A_obs = np.array([[A11    ,  A12 ,   A13 ,  0., 0., 0.],  # [vx]
                    [0    ,  A22 ,   A23  ,  0., 0., 0.],  # [vy]
                    [0    ,   A32 ,    A33  ,  0., 0., 0.],  # [wz]
                    [A41   ,  A42 ,   0. ,   0., 0, 0. ],  # [X]
                    [A51    ,  A52 ,   0. ,  0., 0., 0.],  # [Y]
                    [0    ,  0 ,   1. ,  0., 0., 0. ]]) # [theta]

    
    # print ('self.A_obs',self.A_obs,'self.B_obs',self.B_obs)
    
    return A_obs, B_obs

def L_Computation(vx,vy,w,theta,delta,LQR_gain,sched_var,seq):    

    
    sched_vx    = sched_var[0]
    sched_vy    = sched_var[1]
    sched_w     = sched_var[2]
    sched_theta = sched_var[3]
    sched_delta = sched_var[4]
    
    M_vx_min      = (sched_vx[1] - vx) / (sched_vx[1] - sched_vx[0] );
    M_vy_min      = (sched_vy[1] - vy) / (sched_vy[1] - sched_vy[0] );
    M_w_min       = (sched_w[1] - w) / (sched_w[1] - sched_w[0]); 
    M_theta_min   = (sched_theta[1] - theta) / (sched_theta[1] - sched_theta[0]); 
    M_delta_min   = (sched_delta[1] - delta) / (sched_delta[1] - sched_delta[0]); 

    M_vx_max      = (1 - M_vx_min);      
    M_vy_max      = (1 - M_vy_min);      
    M_w_max       = (1 - M_w_min);       
    M_theta_max   = (1 - M_theta_min);   
    M_delta_max   = (1 - M_delta_min);   

    M_vx          = [M_vx_min, M_vx_max];   
    M_vy          = [M_vy_min, M_vy_max];   
    M_w           = [M_w_min, M_w_max];     
    M_theta       = [M_theta_min, M_theta_max];     
    M_delta       = [M_delta_min, M_delta_max];     


    if vx > sched_vx[1] or vx < sched_vx[0]:
        print( '[ESTIMATOR/L_Gain_Comp]: Vx is out of the polytope ...' )
    elif vy > sched_vy[1] or vy < sched_vy[0]:
        print( '[ESTIMATOR/L_Gain_Comp]: Vy is out of the polytope ...' )
    elif delta > sched_delta[1] or delta < sched_delta[0]:
        print( '[ESTIMATOR/L_Gain_Comp]: Steering is out of the polytope ... = ',delta)


    mu = np.zeros((seq.shape[0],1))
    L_gain  = np.zeros((LQR_gain[:,:,1].shape[0], 5))

    for i in range(seq.shape[0]):
        mu[i] = M_vx[seq[i,0]] * M_vy[seq[i,1]] * M_w[seq[i,2]] * M_theta[seq[i,3]] * M_delta[seq[i,4]];
        L_gain  = L_gain  + mu[i]*LQR_gain[:,:,i];

    return L_gain



def data_retrive(msg, est_msg):

    msg.timestamp_ms = 0
    msg.X  = est_msg[3]
    msg.Y  = est_msg[4]
    msg.roll  = 0
    msg.yaw  = est_msg[5]
    msg.pitch  = 0
    msg.vx  = est_msg[0]
    msg.vy  = est_msg[1]
    msg.yaw_rate  = est_msg[2]
    msg.ax  = 0
    msg.ay  = 0
    msg.s  = 0
    msg.x  = 0
    msg.y  = 0

    return msg

def data_retrive_est(msg, est_msg, yaw_measured, AC_sig, CC_sig):

    msg.timestamp_ms = 0
    msg.X  = est_msg[3]
    msg.Y  = est_msg[4]
    # msg.X  = AC_sig
    # msg.Y  = CC_sig
    msg.roll  = 0
    msg.yaw  = est_msg[5]
    msg.pitch  = 0
    msg.vx  = est_msg[0]
    msg.vy  = est_msg[1]
    msg.yaw_rate  = est_msg[2]
    msg.ax  = AC_sig
    msg.ay  = CC_sig
    msg.s  = yaw_measured
    msg.x  = 0
    msg.y  = 0

    return msg

def meas_retrive(msg, est_msg):

    msg.timestamp_ms = 0
    msg.X  = est_msg[2]
    msg.Y  = est_msg[3]
    msg.roll  = 0
    msg.yaw  = est_msg[4]
    msg.pitch  = 0
    msg.vx  = est_msg[0]
    msg.vy  = 0
    msg.yaw_rate  = est_msg[1]
    msg.ax  = 0
    msg.ay  = 0
    msg.s  = 0
    msg.x  = 0
    msg.y  = 0

    return msg

def load_LQRgain():

    LQR_gain = np.array(sio.loadmat(gain_path)['data']['Lmi'].item())
    seq = sio.loadmat(gain_path)['data']['sequence'].item()
    seq = seq - 1 ##matlab index to python index
    sched_var = sio.loadmat(gain_path,matlab_compatible = 'True')['data']['sched_var'].item()
    
    return LQR_gain, seq, sched_var

def load_switchingLQRgain():

    LQR_gain1 = np.array(sio.loadmat(gain_path)['data']['Lmi1'].item())
    LQR_gain2 = np.array(sio.loadmat(gain_path)['data']['Lmi2'].item())
    LQR_gain3 = np.array(sio.loadmat(gain_path)['data']['Lmi3'].item())
    LQR_gain4 = np.array(sio.loadmat(gain_path)['data']['Lmi4'].item())
    
    LQR_gain = np.array([LQR_gain1, LQR_gain2, LQR_gain3, LQR_gain4])

    seq_1 = sio.loadmat(gain_path)['data']['sequence_1'].item()
    seq_1 = seq_1 - 1 ##matlab index to python index

    seq_2 = sio.loadmat(gain_path)['data']['sequence_2'].item()
    seq_2 = seq_2 - 1 ##matlab index to python index
    
    seq_3 = sio.loadmat(gain_path)['data']['sequence_3'].item()
    seq_3 = seq_3 - 1 ##matlab index to python index
    
    seq_4 = sio.loadmat(gain_path)['data']['sequence_4'].item()
    seq_4 = seq_4 - 1 ##matlab index to python index
    
    seq = np.array([seq_1, seq_2, seq_3, seq_4])

    sched_var = sio.loadmat(gain_path,matlab_compatible = 'True')['data']['sched_var'].item()

    sched_var1 = [sched_var[0], sched_var[1], sched_var[2], sched_var[3], sched_var[7]]
    sched_var2 = [sched_var[0], sched_var[1], sched_var[2], sched_var[4], sched_var[7]]
    sched_var3 = [sched_var[0], sched_var[1], sched_var[2], sched_var[5], sched_var[7]]
    sched_var4 = [sched_var[0], sched_var[1], sched_var[2], sched_var[6], sched_var[7]]

    sched_var = np.array([sched_var1, sched_var2, sched_var3, sched_var4])
    return LQR_gain, seq, sched_var


def constrainAngle(x):
    
    x = fmod(x + pi,2*pi);
    if (x < 0):
        x += 2*pi;
    return x - pi;

# // convert to [-360,360]
def angleConv(angle):
    return fmod(constrainAngle(angle),2*pi);

def angleDiff(a,b):
    dif = fmod(b - a + pi,2*pi);
    if (dif < 0):
        dif += 2*pi;
    return dif - pi;

def unwrap(previousAngle,newAngle):
    return previousAngle - angleDiff(newAngle,angleConv(previousAngle))



def yaw_correction(angle):
    eps = 0.0
    if angle < 0:
        angle = 2*pi - abs(angle)
    elif angle > 2*pi - eps:
        angle = angle%(2.0*pi)
    return angle

def wrap(angle):
    eps = 0.00
    if angle < -np.pi + eps:
        w_angle = 2 * np.pi + angle -eps
    elif angle > np.pi - eps :
        w_angle = angle - 2 * np.pi + eps 
    
    elif angle > 2*np.pi - eps :
        w_angle = angle%(2.0*pi)
    
    elif angle < -2*np.pi + eps :
        w_angle =  -(angle%(2.0*pi))

    else:
        w_angle = angle

    return w_angle

def yaw_smooth(angle_cur, angle_past):
    
    eps = 0.02 ### Boundary near 0 and 2pi for considering the crossing of axis.
    
    CC = False
    AC = False
    # print round(angle_cur,2),round(angle_past,2)
    if (round(2*pi,2) - eps) <= round(angle_cur,2) <= round(2*pi,2) and (round(angle_past,2) <= eps) and (round(angle_past,2) >= 0.0):
        # print "clockwise changes"
        CC = True
        
    if ((round(2*pi,2) -eps) <= round(angle_past,2) <= round(2*pi,2)) and (eps >= round(angle_cur,2) >= 0.0):
#         print "anticlockwise changes"
        AC = True
    return CC, AC


def yaw_error_throw():
    try:
        raise Exception('general exceptions not caught by specific handling')
    except ValueError as e:
        print('Error in yaw transformation')



def main():
    rospy.init_node('switching_lqr_state_estimation', anonymous=True)

    loop_rate   = rospy.get_param("switching_lqr_observer/publish_frequency")
    rate        = rospy.Rate(loop_rate)
    time0       = rospy.get_rostime().to_sec()
    
    counter     = 0
    record_data =    rospy.get_param("switching_lqr_observer/record_data")
    visualization  = rospy.get_param("switching_lqr_observer/visualization")
    sim_on  = rospy.get_param("switching_lqr_observer/sim_on")

    LQR_gain, seq, sched_var =load_switchingLQRgain()

    N_enc  = rospy.get_param("switching_lqr_observer/enc_MA_window")
    N_fcam = rospy.get_param("switching_lqr_observer/fcam_MA_window")
    N_imu  = rospy.get_param("switching_lqr_observer/imu_MA_window")

    lidar_pose_on          = rospy.get_param("switching_lqr_observer/lidar_pose")
    cam_pose_on            = rospy.get_param("switching_lqr_observer/cam_pose")
    fusion_cam_lidar_on    = rospy.get_param("switching_lqr_observer/fusion_cam_lidar")


    if lidar_pose_on == True:
        lidar  = lidar_pose()
    
    if cam_pose_on == True:
        fcam   = fiseye_cam(time0, N_fcam)
        fcam_trans_msg = sensorReading()
        fcam_trans_pub  = rospy.Publisher('fcam_transformed_info', sensorReading, queue_size=1)
    
    if fusion_cam_lidar_on == True:
        lidar  = lidar_pose()
        fcam   = fiseye_cam(time0, N_fcam)
        fcam_trans_msg = sensorReading()
        fcam_trans_pub  = rospy.Publisher('fcam_transformed_info', sensorReading, queue_size=1)


    enc    = motor_encoder(time0, N_enc)
    imu    = IMU(time0, N_imu)


    if sim_on == False:
        if lidar_pose_on and cam_pose_on:

            time.sleep(1)
            print  "yaw_offset", fcam.yaw
            imu.yaw_offset = imu.yaw - fcam.yaw
            time.sleep(1)

        else:
            if lidar_pose_on == True:
                time.sleep(1)
                print  "yaw_offset ", lidar.yaw
                imu.yaw_offset = imu.yaw -  lidar.yaw
                time.sleep(1)



            if cam_pose_on == True:
                time.sleep(1)
                print  "yaw_offset", fcam.yaw
                imu.yaw_offset = imu.yaw - fcam.yaw
                time.sleep(1)

    control_input = vehicle_control(time0)
    
    if visualization == True:
        x_lim = 10
        y_lim = 10
        (fig, axtr, line_est, line_ol, line_meas, rec_est, rec_ol, rec_meas) = _initializeFigure_xy(x_lim,y_lim)

        ol_x_his     = []
        est_x_his    = []
        meas_x_his   = []
        ol_y_his     = []
        est_y_his    = []
        meas_y_his   = []


    # delay  = 5
    # offset = pi/2
    print ("<<<< Initializing IMU orientation >>>>")
    # imu.calibrate_imu(delay,offset)    
    # fcam.calibrate_fcam(delay,R_tf)
    print ("<<<< ORIGIN SET AND CALIBRATION DONE >>>>")


    ###### observation matrix ######
    C       =  np.array([[1, 0, 0, 0, 0, 0], 
                         [0, 0, 1, 0, 0, 0],
                         [0, 0, 0, 1, 0, 0],
                         [0, 0, 0, 0, 1, 0],
                         [0, 0, 0, 0, 0, 1]]) 


    vy = 0.0





    # if sim_on == True:
    #     x_init  = rospy.get_param("switching_lqr_observer/x_tf")
    #     y_init  = rospy.get_param("switching_lqr_observer/y_tf")
    #     yaw_init  = rospy.get_param("switching_lqr_observer/yaw_tf")
    #     est_state = np.array([enc.vx, vy, imu.yaw_rate, x_init, y_init, yaw_init ]).T

    # # est_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, fcam.yaw ]).T
    # # est_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_correction(fcam.yaw) ]).T
    # # est_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, imu.yaw ]).T
    # est_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_curr ]).T

    
    est_state_msg = sensorReading()
    est_state_pub  = rospy.Publisher('est_state_info', sensorReading, queue_size=1)
    
    #### Open loop simulation ###
    # ol_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, fcam.yaw ]).T
    # ol_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_correction(fcam.yaw) ]).T
    # ol_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, imu.yaw ]).T
    # ol_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_curr ]).T
    
    ol_state_msg = sensorReading()
    ol_state_pub  = rospy.Publisher('ol_state_info', sensorReading, queue_size=1)

    meas_state_pub  = rospy.Publisher('meas_state_info', sensorReading, queue_size=1)
    meas_state_msg = sensorReading()
    

    control_data = control()
    
    curr_time = rospy.get_rostime().to_sec() - time0
     
    prev_time = curr_time 
    
    u = [0,0]
    
    lidar_state_test = np.zeros(6)

    lidar_meas_his = []
    cam_meas_his = []
    fused_meas_his = []
    ol_state_his = []
    est_state_his = []
    control_his = []
    full_meas_his = []

    dt = 1.0/loop_rate

    angle_past = imu.yaw

    yaw_curr = (imu.yaw)



    if cam_pose_on == True:
        est_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_curr ]).T
        ol_state = np.array([enc.vx, vy, imu.yaw_rate, fcam.X, fcam.Y, yaw_curr ]).T

    if lidar_pose_on == True:
        est_state = np.array([enc.vx, vy, imu.yaw_rate, lidar.X, lidar.Y, yaw_curr ]).T
        ol_state = np.array([enc.vx, vy, imu.yaw_rate, lidar.X, lidar.Y, yaw_curr ]).T
    
    if fusion_cam_lidar_on == True:
        est_state = np.array([enc.vx, vy, imu.yaw_rate, lidar.X, lidar.Y, yaw_curr ]).T
        ol_state = np.array([enc.vx, vy, imu.yaw_rate, lidar.X, lidar.Y, yaw_curr ]).T


    angle_fused_on = False

    while not (rospy.is_shutdown()):
        curr_time = rospy.get_rostime().to_sec() - time0

        u = np.array([control_input.duty_cycle, control_input.steer]).T

        if angle_fused_on == True:
    
            alpha_angle = 0.2

            angle_cur = alpha_angle*imu.yaw + (1 - alpha_angle)*lidar.yaw
    
        else:

            angle_cur = imu.yaw

        # angle_cur = lidar.yaw
        
        angle_acc = unwrap(angle_past, angle_cur)  

        angle_past = angle_acc
        
        # angle_acc = imu.yaw
        # angle_acc = lidar.yaw




        if cam_pose_on == True:
            y_meas = np.array([enc.vx, imu.yaw_rate, fcam.X, fcam.Y, angle_acc]).T 

            fcam_trans_msg.X = fcam.Y
            fcam_trans_msg.Y = fcam.X
            fcam_trans_pub.publish(fcam_trans_msg)

            if record_data == True:
                cam_meas_his.append([fcam.X, fcam.Y])
                full_meas_his.append(y_meas)


        if lidar_pose_on == True:
            y_meas = np.array([enc.vx, imu.yaw_rate, lidar.X, lidar.Y, angle_acc]).T 

            if record_data == True:
                lidar_meas_his.append([lidar.X, lidar.Y])
                full_meas_his.append(y_meas)


        if fusion_cam_lidar_on == True:

            alpha = 0.2
            X_curr = alpha*fcam.X + (1-alpha)*lidar.X 
            Y_curr = alpha*fcam.Y + (1-alpha)*lidar.Y
            y_meas = np.array([enc.vx, imu.yaw_rate, X_curr, Y_curr, angle_acc]).T 


            fcam_trans_msg.X = fcam.Y
            fcam_trans_msg.Y = fcam.X
            fcam_trans_pub.publish(fcam_trans_msg)

            if record_data == True:

                lidar_meas_his.append([lidar.X, lidar.Y])
                cam_meas_his.append([fcam.X, fcam.Y])
                fused_meas_his.append([X_curr, Y_curr])

                full_meas_his.append(y_meas)
        # y_meas = np.array([enc.vx, imu.yaw_rate, fcam.X, fcam.Y, fcam.yaw]).T
        # y_meas = np.array([enc.vx, imu.yaw_rate, fcam.X, fcam.Y, yaw_correction(fcam.yaw)]).T
        # y_meas = np.array([enc.vx, imu.yaw_rate, fcam.X_MA, fcam.Y_MA, imu.yaw]).T 
        # y_meas = np.array([enc.vx, imu.yaw_rate, fcam.X_MA, fcam.Y_MA, yaw_correction(imu.yaw)]).T 




        dt = curr_time - prev_time 
        
        if  abs(u[0]) > 0.05: # or abs(enc.vx_MA) > 0.0:


            yaw_trans = (est_state[5] + pi) % (2 * pi) - pi

            if 0 <= yaw_trans <= pi/2:
            # % 1st quadrant
                 L_gain = L_Computation(est_state[0], est_state[1], est_state[2], yaw_trans, u[1], LQR_gain[0], sched_var[0], seq[0])
               
            elif pi/2 < yaw_trans <= pi:
            # % 2nd quadrant
                 L_gain = L_Computation(est_state[0], est_state[1], est_state[2], yaw_trans, u[1], LQR_gain[1], sched_var[1], seq[1])
                    
            elif -pi <= yaw_trans <= -pi/2:
            # % 3rd quadrant
                 L_gain = L_Computation(est_state[0], est_state[1], est_state[2], yaw_trans, u[1], LQR_gain[2], sched_var[2], seq[2])
                
            elif (-pi/2 < yaw_trans < 0):
            # % 4th quadrant
                 L_gain = L_Computation(est_state[0], est_state[1], est_state[2], yaw_trans, u[1], LQR_gain[3], sched_var[3], seq[3])
                
            else:
                
                print "est theta", yaw_trans, yaw_trans*180.0/pi 

                display("ERROR Normalize the theta")



                yaw_error_throw()


            ####### LQR ESTIMATION ########
            # A_obs, B_obs = Continuous_AB_Comp(est_state[0], est_state[1], est_state[2], est_state[5], u[1])
            
            A_obs, B_obs  = LPV_model(est_state, u)
# 
            # A_obs, B_obs = Continuous_AB_Comp(est_state, u)

            # # L_gain = L_Computation(est_state[0], est_state[1], est_state[2], est_state[5], u[1], LQR_gain, sched_var, seq)
            
            est_state  = est_state + ( dt * np.dot( ( A_obs - np.dot(L_gain, C) ), est_state )
                            +    dt * np.dot(B_obs, u)
                            +  
                              dt * np.dot(L_gain, y_meas) )
            
            # state_curr = dynamic(est_state,u)
            # est_state  = state_curr + np.dot(L_gain, (y_meas - np.dot(C,state_curr))) 
            


            # est_state  = est_state + ( dt * np.dot( ( A_obs - np.dot(L_gain, C) ), est_state )
            #                 +    dt * np.dot(B_obs, u)
            #                 +    dt * np.dot(L_gain, y_meas) )
            
            # print ("time taken for estimation ={}".format(rospy.get_rostime().to_sec() - time0 - curr_time))
            
            ##### OPEN LOOP SIMULATION ####
            # A_sim, B_sim = Continuous_AB_Comp(ol_state[0], ol_state[1], ol_state[2], ol_state[5], u[1])


            # A_sim, B_sim = Continuous_AB_Comp(y_meas[0], ol_state[1], y_meas[1], y_meas[4], u[1])
# 
            # ol_meas = np.array([y_meas[0], ol_state[1], y_meas[1], y_meas[2], y_meas[3], y_meas[4]]).T
    
            # ol_state = dynamic(ol_meas,u)
    
            # ol_state = ol_state + dt*(np.dot(A_sim,ol_meas) + np.dot(B_sim,u)) 

            # ol_state = ol_state + dt*(np.dot(A_sim,ol_state) + np.dot(B_sim,u)) 



        if abs(u[0]) <= 0.05:
                #     # vehicle_sim.vehicle_model(u, simulator_dt)
                    # if vehicle_sim.vx <= 0.01 :
            est_state[:-3] = 0.000001 
            ol_state[:-3] = 0.000001


        print "\n <<<<<<<<< PRE WRAP >>>>>>>>>>>>>"
        print "est_state",est_state
        print "ol_state", ol_state


        # est_state[5] = wrap(est_state[5])
        # ol_state[5] = wrap(ol_state[5])
        # est_state[5] = yaw_correction(est_state[5])
        # ol_state[5] = yaw_correction(ol_state[5])
        
        print "\n <<<<<<<<< STATS >>>>>>>>>>>>>"
        print "measured states", y_meas
        print "est_state",est_state
        print "ol_state", ol_state
        print "input u", u
        print "dt", dt

        # AC_sig = lidar.X
        # CC_sig = lidar.Y

        # lidar_state_test[3] = lidar.X
        # lidar_state_test[4] = lidar.Y
        # lidar_state_test[5] = lidar.yaw


        AC_sig = 0.0
        CC_sig = 0.0

        lidar_state_test[3] = 0.0
        lidar_state_test[4] = 0.0
        lidar_state_test[5] = 0.0



        est_msg = data_retrive_est(est_state_msg, est_state, y_meas[-1], AC_sig, CC_sig)
        est_state_pub.publish(est_msg) ## remember we want to check the transformed yaw angle for debugging that's why 
        
        # meas_est_state =  np.array([enc.vx_MA, est_state[1], imu.yaw_rate_MA, lidar.X_MA, lidar.Y_MA, angle_acc]).T 
        # meas_est_msg = data_retrive_est(est_state_msg, meas_est_state, y_meas[-1], AC_sig, CC_sig)

                                                                                    #publishing this information in the topic of "s" which is not used for any purpose. 
        # est_state_pub.publish(meas_est_msg)

        ol_state_pub.publish(data_retrive(ol_state_msg, ol_state))

        meas_msg = meas_retrive(meas_state_msg, y_meas)
        meas_state_pub.publish(meas_msg)



        if record_data == True:

            ol_state_his.append(ol_state)
            est_state_his.append(est_state)
            control_his.append(u)




        prev_time = curr_time 

        rate.sleep()


    if record_data == True:
        path = ('/').join(__file__.split('/')[:-2]) + '/data/' 
            
        now = datetime.datetime.now()
        # path = path + now.strftime("d%d_m%m_y%Y/")
        path = path + now.strftime("d%d_m%m_y%Y_hr%H_min%M_sec%S")

        if not os.path.exists(path):
            os.makedirs(path)


        data = {'full_meas': np.array( full_meas_his), 'lidar_pose': np.array( lidar_meas_his), 'cam_pose': np.array(cam_meas_his), 'fused_pose': np.array(fused_meas_his), 'ol_state': np.array(ol_state_his), 'est_state': np.array( est_state_his), 'control': np.array( control_his)}

        path = path + '/observer_data'
        
        np.save(path, data)


if __name__ == '__main__':
    try:
        main()
    except rospy.ROSInterruptException:
        pass


'''
sensorReading message >>>>>>>>>


int64 timestamp_ms
float64 X
float64 Y
float64 roll
float64 yaw
float64 pitch
float64 vx
float64 vy
float64 yaw_rate
float64 ax
float64 ay
float64 s
float64 x
float64 y

'''