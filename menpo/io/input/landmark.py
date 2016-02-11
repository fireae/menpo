from collections import OrderedDict, namedtuple
import json
import warnings
from itertools import groupby, chain

import numpy as np
from scipy.sparse import csr_matrix

from menpo.shape import PointCloud, PointUndirectedGraph, LandmarkGroup
from menpo.transform import Scale
from .base import Importer


class LandmarkImporter(Importer):
    """
    Abstract base class for importing landmarks.

    Parameters
    ----------
    filepath : string
        Absolute filepath of the landmarks.
    """

    def __init__(self, filepath):
        super(LandmarkImporter, self).__init__(filepath)
        self.pointcloud = None

    def build(self, asset=None):
        """
        Overrides the :meth:`build <menpo.io.base.Importer.build>` method.

        Parse the landmark format and return the constructed :map:`PointCloud`
        or subclass.

        Parameters
        ----------
        asset : `object`, optional
            The asset that the landmarks are being built for. Can be used to
            adjust landmarks as necessary (e.g. rescaling image landmarks
            from 0-1 to image.shape)

        Returns
        -------
        landmark_group : :map:`PointCloud` or subclass
            The landmark group parsed from the file.
            Every point will be labelled.
        """
        self._parse_format(asset=asset)
        return self.pointcloud

    def _parse_format(self, asset=None):
        r"""
        Read the landmarks file from disk, parse it in to semantic labels and
        :map:`PointCloud`.

        Set the `self.pointcloud` attribute.
        """
        raise NotImplementedError()


ASFPath = namedtuple('ASFPath', ['path_num', 'path_type', 'xpos', 'ypos',
                                 'point_num', 'connects_from', 'connects_to'])


class ASFImporter(LandmarkImporter):
    r"""
    Abstract base class for an importer for the ASF file format.

    For images, the `x` and `y` axes are flipped such that the first axis is
    `y` (height in the image domain).

    Currently only open and closed path types are supported.

    Landmark labels:

    +---------+
    | label   |
    +=========+
    | all     |
    +---------+

    References
    ----------
    .. [1] http://www2.imm.dtu.dk/~aam/datasets/datasets.html
    """
    def __init__(self, filepath):
        super(ASFImporter, self).__init__(filepath)

    def _parse_format(self, asset=None):
        with open(self.filepath, 'r') as f:
            landmarks = f.read()

        # Remove comments and blank lines
        landmarks = [l for l in landmarks.splitlines()
                     if (l.rstrip() and not '#' in l)]

        # Pop the front of the list for the number of landmarks
        count = int(landmarks.pop(0))
        # Pop the last element of the list for the image_name
        image_name = landmarks.pop()

        xs = np.empty([count, 1])
        ys = np.empty([count, 1])
        connectivity = []

        # Only unpack the first 7 (the last 3 are always 0)
        split_landmarks = [ASFPath(*landmarks[i].split()[:7])
                           for i in range(count)]
        paths = [list(g) for k, g in groupby(split_landmarks, lambda x: x[0])]
        vert_index = 0
        for path in paths:
            if path:
                path_type = path[0].path_type
            for vertex in path:
                # Relative coordinates, will be scaled by the image size
                xs[vert_index, ...] = float(vertex.xpos)
                ys[vert_index, ...] = float(vertex.ypos)
                vert_index += 1
                # If True, isolated point
                if not (vertex.connects_from == vertex.connects_to and
                        vertex.connects_to == vertex.point_num):
                    # Connectivity is defined by connects_from and connects_to
                    # as well as the path_type:
                    #   Bit 1: Outer edge point/Inside point
                    #   Bit 2: Original annotated point/Artificial point
                    #   Bit 3: Closed path point/Open path point
                    #   Bit 4: Non-hole/Hole point
                    # For now we only parse cases 0 and 4 (closed or open)
                    connectivity.append((int(vertex.point_num),
                                         int(vertex.connects_to)))
            if path_type == '0':
                connectivity.append((int(path[-1].point_num),
                                     int(path[0].point_num)))

        connectivity = np.vstack(connectivity)
        points = np.hstack([ys, xs])
        if asset is not None:
            # we've been given an asset. As ASF files are normalized,
            # fix that here
            points = Scale(np.array(asset.shape)).apply(points)

        labels_to_masks = OrderedDict(
            [('all', np.ones(points.shape[0], dtype=np.bool))])
        self.pointcloud = LandmarkGroup.init_from_edges(points, connectivity,
                                                        labels_to_masks)


class PTSImporter(LandmarkImporter):
    r"""
    Importer for the PTS file format. Assumes version 1 of the format.

    Implementations of this class should override the :meth:`_build_points`
    which determines the ordering of axes. For example, for images, the
    `x` and `y` axes are flipped such that the first axis is `y` (height
    in the image domain).

    Note that PTS has a very loose format definition. Here we make the
    assumption (as is common) that PTS landmarks are 1-based. That is,
    landmarks on a 480x480 image are in the range [1-480]. As Menpo is
    consistently 0-based, we *subtract 1* off each landmark value
    automatically.

    If you want to use PTS landmarks that are 0-based, you will have to
    manually add one back on to landmarks post importing.

    Landmark set label: PTS

    Landmark labels:

    +---------+
    | label   |
    +=========+
    | all     |
    +---------+
    """
    def __init__(self, filepath):
        super(PTSImporter, self).__init__(filepath)

    def _build_points(self, xs, ys):
        r"""
        Determines the ordering of points within the landmarks. For meshes
        `x` is the first axis, where as for images `y` is the first axis.
        """
        raise NotImplementedError()

    def _parse_format(self, asset=None):
        f = open(self.filepath, 'r')
        for line in f:
            if line.split()[0] == '{':
                break
        xs = []
        ys = []
        for line in f:
            if line.split()[0] != '}':
                xpos, ypos = line.split()[0:2]
                xs.append(xpos)
                ys.append(ypos)
        xs = np.array(xs, dtype=np.float).reshape((-1, 1))
        ys = np.array(ys, dtype=np.float).reshape((-1, 1))
        # PTS landmarks are 1-based, need to convert to 0-based (subtract 1)
        points = self._build_points(xs - 1, ys - 1)

        self.pointcloud = PointCloud(points, copy=False)


class LM2Importer(LandmarkImporter):
    r"""
    Importer for the LM2 file format from the bosphorus dataset. This is a 2D
    landmark type and so it is assumed it only applies to images.

    Landmark set label: LM2

    Landmark labels:

    +------------------------+
    | label                  |
    +========================+
    | outer_left_eyebrow     |
    | middle_left_eyebrow    |
    | inner_left_eyebrow     |
    | inner_right_eyebrow    |
    | middle_right_eyebrow   |
    | outer_right_eyebrow    |
    | outer_left_eye_corner  |
    | inner_left_eye_corner  |
    | inner_right_eye_corner |
    | outer_right_eye_corner |
    | nose_saddle_left       |
    | nose_saddle_right      |
    | left_nose_peak         |
    | nose_tip               |
    | right_nose_peak        |
    | left_mouth_corner      |
    | upper_lip_outer_middle |
    | right_mouth_corner     |
    | upper_lip_inner_middle |
    | lower_lip_inner_middle |
    | lower_lip_outer_middle |
    | chin_middle            |
    +------------------------+
    """
    def __init__(self, filepath):
        super(LM2Importer, self).__init__(filepath)

    def _parse_format(self, asset=None):
        with open(self.filepath, 'r') as f:
            landmarks = f.read()

        # Remove comments and blank lines
        landmark_text = [l for l in landmarks.splitlines()
                         if (l.rstrip() and '#' not in l)]

        # First line says how many landmarks there are: 22 Landmarks
        # So pop it off the front
        num_points = int(landmark_text.pop(0).split()[0])
        labels = []

        # The next set of lines defines the labels
        labels_str = landmark_text.pop(0)
        if not labels_str == 'Labels:':
            raise ValueError("LM2 landmarks are incorrectly formatted. "
                             "Expected a list of labels beginning with "
                             "'Labels:' but found '{0}'".format(labels_str))
        for i in range(num_points):
            # Lowercase, remove spaces and replace with underscores
            l = landmark_text.pop(0)
            l = '_'.join(l.lower().split())
            labels.append(l)

        # The next set of lines defines the coordinates
        coords_str = landmark_text.pop(0)
        if not coords_str == '2D Image coordinates:':
            raise ValueError("LM2 landmarks are incorrectly formatted. "
                             "Expected a list of coordinates beginning with "
                             "'2D Image coordinates:' "
                             "but found '{0}'".format(coords_str))
        xs = []
        ys = []
        for i in range(num_points):
            p = landmark_text.pop(0).split()
            xs.append(float(p[0]))
            ys.append(float(p[1]))

        xs = np.array(xs, dtype=np.float).reshape((-1, 1))
        ys = np.array(ys, dtype=np.float).reshape((-1, 1))
        # Flip the x and y
        points = np.hstack([ys, xs])

        # Create the mask whereby there is one landmark per label
        # (identity matrix)
        masks = np.eye(num_points).astype(np.bool)
        masks = np.vsplit(masks, num_points)
        masks = [np.squeeze(m) for m in masks]

        empty_adj_matrix = csr_matrix((num_points, num_points))
        self.pointcloud = LandmarkGroup(points, empty_adj_matrix,
                                        OrderedDict(zip(labels, masks)))


def _ljson_parse_null_values(points_list):
    filtered_points = [np.nan if x is None else x
                       for x in chain(*points_list)]
    return np.array(filtered_points,
                    dtype=np.float).reshape([-1, len(points_list[0])])


def _parse_ljson_v1(lms_dict):
    from menpo.base import MenpoDeprecationWarning
    warnings.warn('LJSON v1 is deprecated. export_landmark_file{s}() will '
                  'only save out LJSON v2 files. Please convert all LJSON '
                  'files to v2 by importing into Menpo and re-exporting to '
                  'overwrite the files.', MenpoDeprecationWarning)
    all_points = []
    labels = []  # label per group
    labels_slices = []  # slices into the full pointcloud per label
    offset = 0
    connectivity = []
    for group in lms_dict['groups']:
        lms = group['landmarks']
        labels.append(group['label'])
        labels_slices.append(slice(offset, len(lms) + offset))
        # Create the connectivity if it exists
        conn = group.get('connectivity', [])
        if conn:
            # Offset relative connectivity according to the current index
            conn = offset + np.asarray(conn)
            connectivity += conn.tolist()
        for p in lms:
            all_points.append(p['point'])
        offset += len(lms)

    points = _ljson_parse_null_values(all_points)
    n_points = points.shape[0]

    labels_to_masks = OrderedDict()
    # go through each label and build the appropriate boolean array
    for label, l_slice in zip(labels, labels_slices):
        mask = np.zeros(n_points, dtype=np.bool)
        mask[l_slice] = True
        labels_to_masks[label] = mask

    return LandmarkGroup.init_from_edges(points, connectivity, labels_to_masks)


def _parse_ljson_v2(lms_dict):
    labels_to_mask = OrderedDict()  # masks into the full pointcloud per label

    points = _ljson_parse_null_values(lms_dict['landmarks']['points'])
    n_points = points.shape[0]
    connectivity = lms_dict['landmarks'].get('connectivity')

    for label in lms_dict['labels']:
        mask = np.zeros(n_points, dtype=np.bool)
        mask[label['mask']] = True
        labels_to_mask[label['label']] = mask

    return LandmarkGroup.init_from_edges(points, connectivity, labels_to_mask)


_ljson_parser_for_version = {
    1: _parse_ljson_v1,
    2: _parse_ljson_v2
}


class LJSONImporter(LandmarkImporter):
    r"""
    Importer for the Menpo JSON format. This is an n-dimensional
    landmark type for both images and meshes that encodes semantic labels in
    the format.

    Landmark set label: JSON

    Landmark labels: decided by file
    """
    def __init__(self, filepath):
        super(LJSONImporter, self).__init__(filepath)

    def _parse_format(self, asset=None):
        with open(self.filepath, 'r') as f:
            # lms_dict is now a dict rep of the JSON
            lms_dict = json.load(f, object_pairs_hook=OrderedDict)
        v = lms_dict.get('version')
        parser = _ljson_parser_for_version.get(v)
        if parser is None:
            raise ValueError("{} has unknown version {} must be "
                             "1, or 2".format(self.filepath, v))
        else:
            self.pointcloud = parser(lms_dict)
