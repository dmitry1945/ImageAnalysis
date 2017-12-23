#!/usr/bin/python

# for all the images in the project image_dir, compute the camera
# poses from the aircraft pose (and camera mounting transform).
# Project the image plane onto an SRTM (DEM) surface for our best
# layout guess (at this point before we do any matching/bundle
# adjustment work.)

import sys
sys.path.insert(0, "/usr/local/opencv3/lib/python2.7/site-packages/")

import argparse
import cPickle as pickle
import cv2
import numpy as np
import os.path

sys.path.append('../lib')
import AC3D
import Groups
import Pose
import ProjectMgr
import SRTM
import transformations

import match_culling as cull

ac3d_steps = 8

parser = argparse.ArgumentParser(description='Set the initial camera poses.')
parser.add_argument('--project', required=True, help='project directory')
parser.add_argument('--texture-resolution', type=int, default=512, help='texture resolution (should be 2**n, so numbers like 256, 512, 1024, etc.')
parser.add_argument('--srtm', action='store_true', help='use srtm elevation')
parser.add_argument('--ground', type=float, help='force ground elevation in meters')
parser.add_argument('--direct', action='store_true', help='use direct pose')

args = parser.parse_args()

proj = ProjectMgr.ProjectMgr(args.project)
proj.load_image_info()
proj.load_features()
proj.undistort_keypoints()

ref = proj.ned_reference_lla

# setup SRTM ground interpolator
sss = SRTM.NEDGround( ref, 6000, 6000, 30 )

print "Loading match points (sba)..."
matches_sba = pickle.load( open( args.project + "/matches_sba", "rb" ) )

# load the group connections within the image set
groups = Groups.load(args.project)

# testing ...
def polyfit2d(x, y, z, order=3):
    ncols = (order + 1)**2
    G = np.zeros((x.size, ncols))
    ij = itertools.product(range(order+1), range(order+1))
    for k, (i,j) in enumerate(ij):
        G[:,k] = x**i * y**j
    m, _, _, _ = np.linalg.lstsq(G, z)
    return m

def polyval2d(x, y, m):
    order = int(np.sqrt(len(m))) - 1
    ij = itertools.product(range(order+1), range(order+1))
    z = np.zeros_like(x)
    for a, (i,j) in zip(m, ij):
        z += a * x**i * y**j
    return z

print "Generating a bivariate b-spline approximation to the stitched surface"
import itertools
import matplotlib.pyplot as plt
from matplotlib import cm
# first determine surface elevation stats so we can discard outliers
z = []
for match in matches_sba:
    used = False
    for p in match[1:]:
        if p[0] in groups[0]:
            used = True
    if used:
        ned = match[0]
        z.append(ned[2])
zavg = np.mean(z)
zstd = np.std(z)
print 'elevation stats:', zavg, zstd

# now build the surface
xfit = []
yfit = []
zfit = []
for match in matches_sba:
    used = False
    for p in match[1:]:
        if p[0] in groups[0]:
            used = True
    if used:
        ned = match[0]
        d = abs(ned[2] - zavg)
        if d <= 2*zstd:
            xfit.append(ned[0])
            yfit.append(ned[1])
            zfit.append(ned[2])
        #else:
        #    # mark this elevation unused
        #    match[0][2] = None
xfit = np.array(xfit)
yfit = np.array(yfit)
zfit = np.array(zfit)
plt.figure()
plt.scatter(xfit, yfit, 100, zfit, cmap=cm.jet)
plt.colorbar()
plt.title("Sparsely sampled function.")

# Fit a 3rd order, 2d polynomial
m = polyfit2d(xfit, yfit, zfit)

# test fit
znew = polyval2d(xfit, yfit, m)
for i in range(len(znew)):
    print polyval2d(xfit[i], yfit[i], m)
    print 'z:', zfit[i], znew[i], zfit[i] - znew[i]
plt.figure()
plt.scatter(xfit, yfit, 100, znew, cmap=cm.jet)
plt.colorbar()
plt.title("Approximation function.")
plt.show()

# compute the uv grid for each image and project each point out into
# ned space, then intersect each vector with the srtm / ground /
# polynomial surface.

# for each image, find all the placed features, and compute an average
# elevation
for image in proj.image_list:
    image.z_list = []
    image.grid_list = []
for i, match in enumerate(matches_sba):
    ned = match[0]
    for p in match[1:]:
        index = p[0]
        proj.image_list[index].z_list.append(-ned[2])
#        proj.image_list[index].z_list.append(znew[i])
for image in proj.image_list:
    if len(image.z_list):
        avg = np.mean(np.array(image.z_list))
        std = np.std(np.array(image.z_list))
    else:
        avg = None
        std = None
    image.z_avg = avg
    image.z_std = std
    print image.name, 'features:', len(image.z_list), 'avg:', avg, 'std:', std

# for fun rerun through the matches and find elevation outliers
outliers = []
for i, match in enumerate(matches_sba):
    ned = match[0]
    error_sum = 0
    for p in match[1:]:
        image = proj.image_list[p[0]]
        dist = abs(-ned[2] - image.z_avg)
        error_sum += dist
    if error_sum > 3 * (image.z_std * len(match[1:])):
        print 'possible outlier match index:', i, error_sum, 'z:', ned[2]
        outliers.append( [error_sum, i] )

result = sorted(outliers, key=lambda fields: fields[0], reverse=True)
for line in result:
    print 'index:', line[1], 'error:', line[0]
    #cull.draw_match(line[1], 1, matches_sba, proj.image_list)
    
depth = 0.0
camw, camh = proj.cam.get_image_params()
#for group in groups:
if True:
    group = groups[0]
    #if len(group) < 3:
    #    continue
    for g in group:
        image = proj.image_list[g]
        print image.name, image.z_avg
        # scale the K matrix if we have scaled the images
        scale = float(image.width) / float(camw)
        K = proj.cam.get_K(scale)
        IK = np.linalg.inv(K)

        grid_list = []
        u_list = np.linspace(0, image.width, ac3d_steps + 1)
        v_list = np.linspace(0, image.height, ac3d_steps + 1)
        #print "u_list:", u_list
        #print "v_list:", v_list
        for v in v_list:
            for u in u_list:
                grid_list.append( [u, v] )
        #print 'grid_list:', grid_list

        if args.direct:
            proj_list = proj.projectVectors( IK, image.get_body2ned(),
                                             image.get_cam2body(), grid_list )
        else:
            print image.get_body2ned_sba()
            proj_list = proj.projectVectors( IK, image.get_body2ned_sba(),
                                             image.get_cam2body(), grid_list )
        #print 'proj_list:', proj_list

        if args.direct:
            ned = image.camera_pose['ned']
        else:
            ned = image.camera_pose_sba['ned']
        print 'ned', image.camera_pose['ned'], ned
        if args.ground:
            pts_ned = proj.intersectVectorsWithGroundPlane(ned,
                                                           args.ground, proj_list)
        elif args.srtm:
            pts_ned = sss.interpolate_vectors(ned, proj_list)
        else:
            print image.name, image.z_avg
            pts_ned = proj.intersectVectorsWithGroundPlane(ned,
                                                           image.z_avg,
                                                           proj_list)
        #print "pts_3d (ned):\n", pts_ned

        # convert ned to xyz and stash the result for each image
        image.grid_list = []
        ground_sum = 0
        for p in pts_ned:
            image.grid_list.append( [p[1], p[0], -(p[2]+depth)] )
            #image.grid_list.append( [p[1], p[0], -(depth)] )
            ground_sum += -p[2]
        depth -= 0.01                # favor last pictures above earlier ones
    
# call the ac3d generator
AC3D.generate(proj.image_list, src_dir=proj.source_dir,
              project_dir=args.project, base_name='direct',
              version=1.0, trans=0.1, resolution=args.texture_resolution)

if not args.ground:
    print 'Avg ground elevation (SRTM):', ground_sum / len(pts_ned)