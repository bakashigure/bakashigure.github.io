DATAPATH = 'data/'
OUTPUTPATH = 'generated_output/'
INPUT_XML = 'page-88.xml'

import os
from pdftabextract.common import read_xml, parse_pages

# Load the XML that was generated with pdftohtml
xmltree, xmlroot = read_xml(os.path.join(DATAPATH, INPUT_XML))

# parse it and generate a dict of pages
pages = parse_pages(xmlroot)

from pprint import pprint

p_num = 1
p = pages[p_num]

print('number', p['number'])
print('width', p['width'])
print('height', p['height'])
print('image', p['image'])
print('the first three text boxes:')
pprint(p['texts'][:3])

import numpy as np
from pdftabextract import imgproc

# get the image file of the scanned page
imgfilebasename = p['image'][:p['image'].rindex('.')]
imgfile = os.path.join(DATAPATH, p['image'])

print("page %d: detecting lines in image file '%s'..." % (p_num, imgfile))

# create an image processing object with the scanned page
iproc_obj = imgproc.ImageProc(imgfile)

# calculate the scaling of the image file in relation to the text boxes coordinate system dimensions
page_scaling_x = iproc_obj.img_w / p['width']   # scaling in X-direction
page_scaling_y = iproc_obj.img_h / p['height']  # scaling in Y-direction

# detect the lines
lines_hough = iproc_obj.detect_lines(canny_kernel_size=3, canny_low_thresh=50, canny_high_thresh=150,
                                     hough_rho_res=1,
                                     hough_theta_res=np.pi/500,
                                     hough_votes_thresh=round(0.2 * iproc_obj.img_w))
print("> found %d lines" % len(lines_hough))

import cv2

# helper function to save an image 
def save_image_w_lines(iproc_obj, imgfilebasename):
    img_lines = iproc_obj.draw_lines(orig_img_as_background=True)
    img_lines_file = os.path.join(OUTPUTPATH, '%s-lines-orig.png' % imgfilebasename)

    print("> saving image with detected lines to '%s'" % img_lines_file)
    cv2.imwrite(img_lines_file, img_lines)

from math import radians, degrees

from pdftabextract.common import ROTATION, SKEW_X, SKEW_Y
from pdftabextract.geom import pt
from pdftabextract.textboxes import rotate_textboxes, deskew_textboxes

# find rotation or skew
# the parameters are:
# 1. the minimum threshold in radians for a rotation to be counted as such
# 2. the maximum threshold for the difference between horizontal and vertical line rotation (to detect skew)
# 3. an optional threshold to filter out "stray" lines whose angle is too far apart from the median angle of
#    all other lines that go in the same direction (no effect here)
rot_or_skew_type, rot_or_skew_radians = iproc_obj.find_rotation_or_skew(radians(0.5),    # uses "lines_hough"
                                                                        radians(1),
                                                                        omit_on_rot_thresh=radians(0.5))

# rotate back or deskew text boxes
needs_fix = True
if rot_or_skew_type == ROTATION:
    print("> rotating back by %f��" % -degrees(rot_or_skew_radians))
    rotate_textboxes(p, -rot_or_skew_radians, pt(0, 0))
elif rot_or_skew_type in (SKEW_X, SKEW_Y):
    print("> deskewing in direction '%s' by %f��" % (rot_or_skew_type, -degrees(rot_or_skew_radians)))
    deskew_textboxes(p, -rot_or_skew_radians, rot_or_skew_type, pt(0, 0))
else:
    needs_fix = False
    print("> no page rotation / skew found")

if needs_fix:
    # rotate back or deskew detected lines
    lines_hough = iproc_obj.apply_found_rotation_or_skew(rot_or_skew_type, -rot_or_skew_radians)

    save_image_w_lines(iproc_obj, imgfilebasename + '-repaired')



# save repaired XML (i.e. XML with deskewed textbox positions)
output_files_basename = INPUT_XML[:INPUT_XML.rindex('.')]
repaired_xmlfile = os.path.join(OUTPUTPATH, output_files_basename + '.repaired.xml')

print("saving repaired XML file to '%s'..." % repaired_xmlfile)
xmltree.write(repaired_xmlfile)

from pdftabextract.clustering import find_clusters_1d_break_dist

MIN_COL_WIDTH = 60 # minimum width of a column in pixels, measured in the scanned pages

# cluster the detected *vertical* lines using find_clusters_1d_break_dist as simple clustering function
# (break on distance MIN_COL_WIDTH/2)
# additionaly, remove all cluster sections that are considered empty
# a cluster is considered empty when the number of text boxes in it is below 10% of the median number of text boxes
# per cluster section
vertical_clusters = iproc_obj.find_clusters(imgproc.DIRECTION_VERTICAL, find_clusters_1d_break_dist,
                                            remove_empty_cluster_sections_use_texts=p['texts'], # use this page's textboxes
                                            remove_empty_cluster_sections_n_texts_ratio=0.1,    # 10% rule
                                            remove_empty_cluster_sections_scaling=page_scaling_x,  # the positions are in "scanned image space" -> we scale them to "text box space"
                                            dist_thresh=MIN_COL_WIDTH/2)
print("> found %d clusters" % len(vertical_clusters))

# draw the clusters
img_w_clusters = iproc_obj.draw_line_clusters(imgproc.DIRECTION_VERTICAL, vertical_clusters)
save_img_file = os.path.join(OUTPUTPATH, '%s-vertical-clusters.png' % imgfilebasename)
print("> saving image with detected vertical clusters to '%s'" % save_img_file)
cv2.imwrite(save_img_file, img_w_clusters)

from pdftabextract.clustering import calc_cluster_centers_1d

page_colpos = np.array(calc_cluster_centers_1d(vertical_clusters)) / page_scaling_x
print('found %d column borders:' % len(page_colpos))
print(page_colpos)

# right border of the second column
col2_rightborder = page_colpos[2]

# calculate median text box height
median_text_height = np.median([t['height'] for t in p['texts']])

# get all texts in the first two columns with a "usual" textbox height
# we will only use these text boxes in order to determine the line positions because they are more "stable"
# otherwise, especially the right side of the column header can lead to problems detecting the first table row
text_height_deviation_thresh = median_text_height / 2
texts_cols_1_2 = [t for t in p['texts']
                  if t['right'] <= col2_rightborder
                     and abs(t['height'] - median_text_height) <= text_height_deviation_thresh]


from pdftabextract.clustering import zip_clusters_and_values
from pdftabextract.textboxes import border_positions_from_texts, split_texts_by_positions, join_texts
from pdftabextract.common import all_a_in_b, DIRECTION_VERTICAL

# get all textboxes' top and bottom border positions
borders_y = border_positions_from_texts(texts_cols_1_2, DIRECTION_VERTICAL)

# break into clusters using half of the median text height as break distance
clusters_y = find_clusters_1d_break_dist(borders_y, dist_thresh=median_text_height/2)
clusters_w_vals = zip_clusters_and_values(clusters_y, borders_y)

# for each cluster, calculate the median as center
pos_y = calc_cluster_centers_1d(clusters_w_vals)
pos_y.append(p['height'])

print('number of line positions:', len(pos_y))


import re

# a (possibly malformed) population number + space + start of city name
pttrn_table_row_beginning = re.compile(r'^[\d Oo][\d Oo]{2,} +[A-Z???]')

# 1. try to find the top row of the table
texts_cols_1_2_per_line = split_texts_by_positions(texts_cols_1_2, pos_y, DIRECTION_VERTICAL,
                                                   alignment='middle',
                                                   enrich_with_positions=True)

# go through the texts line per line
for line_texts, (line_top, line_bottom) in texts_cols_1_2_per_line:
    line_str = join_texts(line_texts)
    if pttrn_table_row_beginning.match(line_str):  # check if the line content matches the given pattern
        top_y = line_top
        break
else:
    top_y = 0


# hints for a footer text box
words_in_footer = ('anzeige', 'annahme', 'ala')

# 2. try to find the bottom row of the table
min_footer_text_height = median_text_height * 1.5
min_footer_y_pos = p['height'] * 0.7
# get all texts in the lower 30% of the page that have are at least 50% bigger than the median textbox height
bottom_texts = [t for t in p['texts']
                if t['top'] >= min_footer_y_pos and t['height'] >= min_footer_text_height]
bottom_texts_per_line = split_texts_by_positions(bottom_texts,
                                                 pos_y + [p['height']],   # always down to the end of the page
                                                 DIRECTION_VERTICAL,
                                                 alignment='middle',
                                                 enrich_with_positions=True)
# go through the texts at the bottom line per line
page_span = page_colpos[-1] - page_colpos[0]
min_footer_text_width = page_span * 0.8
for line_texts, (line_top, line_bottom) in bottom_texts_per_line:
    line_str = join_texts(line_texts)
    has_wide_footer_text = any(t['width'] >= min_footer_text_width for t in line_texts)
    # check if there's at least one wide text or if all of the required words for a footer match
    if has_wide_footer_text or all_a_in_b(words_in_footer, line_str):
        bottom_y = line_top
        break
else:
    bottom_y = p['height']



page_rowpos = [y for y in pos_y if top_y <= y <= bottom_y]
print("> page %d: %d lines between [%f, %f]" % (p_num, len(page_rowpos), top_y, bottom_y))


from pdftabextract.extract import make_grid_from_positions

grid = make_grid_from_positions(page_colpos, page_rowpos)
n_rows = 17
n_cols = 12
print("> page %d: grid with %d rows, %d columns" % (p_num, n_rows, n_cols))


from pdftabextract.common import save_page_grids

page_grids_file = os.path.join(OUTPUTPATH, output_files_basename + '.pagegrids_p3_only.json')
print("saving page grids JSON file to '%s'" % page_grids_file)
save_page_grids({p_num: grid}, page_grids_file)

from pdftabextract.extract import fit_texts_into_grid, datatable_to_dataframe

datatable = fit_texts_into_grid(p['texts'], grid)

df = datatable_to_dataframe(datatable)

df.head(10)

df.to_csv('output.csv', index=False)
df.to_excel('output.xlsx', index=False)