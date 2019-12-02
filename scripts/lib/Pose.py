#!/usr/bin/python

# pose related functions

import csv
import fileinput
import fnmatch
import math
import os
import re

import navpy
from props import getNode

from . import exif
from . import image
from .logger import log
from . import transformations

# this should really be a parameter.  Any aircraft poses that exceed
# this value for either roll or pitch will be ignored.  Oblique photos
# combined with lens distortion become really difficult for the
# optimizer to resolve (especially when it puts points off near the
# horizon.)

# a helpful constant
d2r = math.pi / 180.0
r2d = 180.0 / math.pi

# quaternions represent a rotation from one coordinate system to
# another (i.e. from NED space to aircraft body space).  You can
# back translate a vector against this quaternion to project a camera
# vector into NED space.
#
# body angles represent the aircraft orientation
# camera angles represent the fixed mounting offset of the camera
# relative to the body
# +x axis = forward, +roll = left wing down
# +y axis = right, +pitch = nose down
# +z axis = up, +yaw = nose right


# define the image aircraft poses from Sentera meta data file
def setAircraftPoses(proj, posefile="", order='ypr', max_angle=25.0):
    log("Setting aircraft poses")
    
    meta_dir = os.path.join(proj.project_dir, 'ImageAnalysis', 'meta')
    proj.image_list = []
    
    f = fileinput.input(posefile)
    for line in f:
        line.strip()
        if re.match('^\s*#', line):
            #print("skipping comment ", line)
            continue
        if re.match('^\s*File', line):
            #print("skipping header ", line)
            continue
        field = line.split(',')
        name = field[0]
        lat_deg = float(field[1])
        lon_deg = float(field[2])
        alt_m = float(field[3])
        if order == 'ypr':
            yaw_deg = float(field[4])
            pitch_deg = float(field[5])
            roll_deg = float(field[6])
        elif order == 'rpy':
            roll_deg = float(field[4])
            pitch_deg = float(field[5])
            yaw_deg = float(field[6])
        if len(field) >= 8:
            flight_time = float(field[7])
        else:
            flight_time = -1.0

        found_dir = ''
        if not os.path.isfile( os.path.join(proj.project_dir, name) ):
            log("No image file:", name, "skipping ...")
            continue
        if abs(roll_deg) > max_angle or abs(pitch_deg) > max_angle:
            # fairly 'extreme' attitude, skip image
            log("extreme attitude:", name, "roll:", roll_deg, "pitch:", pitch_deg)
            continue

        base, ext = os.path.splitext(name)
        img = image.Image(meta_dir, base)
        img.set_aircraft_pose(lat_deg, lon_deg, alt_m,
                              yaw_deg, pitch_deg, roll_deg,
                              flight_time)
        log("pose:", name, "yaw=%.1f pitch=%.1f roll=%.1f" % (yaw_deg, pitch_deg, roll_deg))
        proj.image_list.append(img)

# for each image, compute the estimated camera pose in NED space from
# the aircraft body pose and the relative camera orientation
def compute_camera_poses(proj):
    log("Setting camera poses (offset from aircraft pose.)")
    
    mount_node = getNode("/config/camera/mount", True)
    ref_node = getNode("/config/ned_reference", True)
    images_node = getNode("/images", True)

    camera_yaw = mount_node.getFloat("yaw_deg")
    camera_pitch = mount_node.getFloat("pitch_deg")
    camera_roll = mount_node.getFloat("roll_deg")
    print(camera_yaw, camera_pitch, camera_roll)
    body2cam = transformations.quaternion_from_euler(camera_yaw * d2r,
                                                     camera_pitch * d2r,
                                                     camera_roll * d2r,
                                                     "rzyx")

    ref_lat = ref_node.getFloat("lat_deg")
    ref_lon = ref_node.getFloat("lon_deg")
    ref_alt = ref_node.getFloat("alt_m")

    for image in proj.image_list:
        ac_pose_node = image.node.getChild("aircraft_pose", True)
        #cam_pose_node = images_node.getChild(name + "/camera_pose", True)
        
        aircraft_lat = ac_pose_node.getFloat("lat_deg")
        aircraft_lon = ac_pose_node.getFloat("lon_deg")
        aircraft_alt = ac_pose_node.getFloat("alt_m")
        ned2body = []
        for i in range(4):
            ned2body.append( ac_pose_node.getFloatEnum("quat", i) )
        
        ned2cam = transformations.quaternion_multiply(ned2body, body2cam)
        (yaw_rad, pitch_rad, roll_rad) = transformations.euler_from_quaternion(ned2cam, "rzyx")
        ned = navpy.lla2ned( aircraft_lat, aircraft_lon, aircraft_alt,
                             ref_lat, ref_lon, ref_alt )

        image.set_camera_pose(ned, yaw_rad*r2d, pitch_rad*r2d, roll_rad*r2d)

# make a pix4d pose file from project image metadata
def make_pix4d(image_dir, force_altitude=None, force_heading=None, yaw_from_groundtrack=False):
    # load list of images
    files = []
    for file in os.listdir(image_dir):
        if fnmatch.fnmatch(file, "*.jpg") or fnmatch.fnmatch(file, "*.JPG"):
            files.append(file)
    files.sort()

    # save some work if true
    images_have_yaw = False

    images = []
    # read image exif timestamp (and convert to unix seconds)
    for file in files:
        full_name = os.path.join(image_dir, file)
        # print(full_name)
        lon, lat, alt, unixtime, yaw_deg = exif.get_pose(full_name)

        line = [file, lat, lon]
        if not force_altitude:
            line.append(alt)
        else:
            line.append(force_altitude)

        line.append(0)          # assume zero pitch
        line.append(0)          # assume zero roll
        if force_heading is not None:
            line.append(force_heading)
        elif yaw_deg is not None:
            images_have_yaw = True
            line.append(yaw_deg)
        else:
            # no yaw info found in metadata
            line.append(0)
            
        images.append(line)

    if not force_heading and not images_have_yaw or yaw_from_groundtrack:
        # do extra work to estimate yaw heading from gps ground track
        for i in range(len(images)):
            if i > 0:
                prev = images[i-1]
            else:
                prev = None
            cur = images[i]
            if i < len(images)-1:
                next = images[i+1]
            else:
                next = None

            if not prev is None:
                (prev_hdg, rev_course, prev_dist) = \
                    wgs84.geo_inverse( prev[1], prev[2], cur[1], cur[2] )
            else:
                prev_hdg = 0.0
                prev_dist = 0.0
            if not next is None:
                (next_hdg, rev_course, next_dist) = \
                    wgs84.geo_inverse( cur[1], cur[2], next[1], next[2] )
            else:
                next_hdg = 0.0
                next_dist = 0.0

            prev_hdgx = math.cos(prev_hdg*d2r)
            prev_hdgy = math.sin(prev_hdg*d2r)
            next_hdgx = math.cos(next_hdg*d2r)
            next_hdgy = math.sin(next_hdg*d2r)
            avg_hdgx = (prev_hdgx*prev_dist + next_hdgx*next_dist) / (prev_dist + next_dist)
            avg_hdgy = (prev_hdgy*prev_dist + next_hdgy*next_dist) / (prev_dist + next_dist)
            avg_hdg = math.atan2(avg_hdgy, avg_hdgx)*r2d
            if avg_hdg < 0:
                avg_hdg += 360.0
            #print("%d %.2f %.1f %.2f %.1f %.2f" % (i, prev_hdg, prev_dist, next_hdg, next_dist, avg_hdg))
            images[i][6] = avg_hdg

    # sanity check
    output_file = os.path.join(image_dir, "pix4d.csv")
    if os.path.exists(output_file):
        log(output_file, "exists, please rename it and rerun this script.")
        quit()
    log("Creating pix4d image pose file:", output_file, "images:", len(files))
    
    # traverse the image list and create output csv file
    with open(output_file, 'w') as csvfile:
        writer = csv.DictWriter( csvfile,
                                 fieldnames=["File Name",
                                             "Lat (decimal degrees)",
                                             "Lon (decimal degrees)",
                                             "Alt (meters MSL)",
                                             "Roll (decimal degrees)",
                                             "Pitch (decimal degrees)",
                                             "Yaw (decimal degrees)"] )
        writer.writeheader()
        for line in images:
            image = line[0]
            lat_deg = line[1]
            lon_deg = line[2]
            alt_m = line[3]
            roll_deg = line[4]
            pitch_deg = line[5]
            yaw_deg = line[6]
            #print(image, lat_deg, lon_deg, alt_m)
            writer.writerow( { "File Name": os.path.basename(image),
                               "Lat (decimal degrees)": "%.10f" % lat_deg,
                               "Lon (decimal degrees)": "%.10f" % lon_deg,
                               "Alt (meters MSL)": "%.2f" % alt_m,
                               "Roll (decimal degrees)": "%.2f" % roll_deg,
                               "Pitch (decimal degrees)": "%.2f" % pitch_deg,
                               "Yaw (decimal degrees)": "%.2f" % yaw_deg } )
