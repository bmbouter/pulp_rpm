# -*- coding: utf-8 -*-
#
# Copyright © 2013 Red Hat, Inc.
#
# This software is licensed to you under the GNU General Public
# License as published by the Free Software Foundation; either version
# 2 of the License (GPLv2) or (at your option) any later version.
# There is NO WARRANTY for this software, express or implied,
# including the implied warranties of MERCHANTABILITY,
# NON-INFRINGEMENT, or FITNESS FOR A PARTICULAR PURPOSE. You should
# have received a copy of GPLv2 along with this software; if not, see
# http://www.gnu.org/licenses/old-licenses/gpl-2.0.txt.

import os.path
import logging
from pulp_rpm.common import constants, version_utils

_LOGGER = logging.getLogger(__name__)


class Package(object):
    UNIT_KEY_NAMES = tuple()
    TYPE = None
    def __init__(self, local_vars):
        for name in self.UNIT_KEY_NAMES:
            setattr(self, name, local_vars[name])
        self.metadata = local_vars.get('metadata', {})

    @property
    def unit_key(self):
        key = {}
        for name in self.UNIT_KEY_NAMES:
            key[name] = getattr(self, name)
        return key

    @property
    def unit_key_as_typed_tuple(self):
        """
        This is a memory-efficient way to represent a unit key. I tested three
        representations:

        1) instances of RPM with metadata as empty dict
        2) RPM().unit_key
        3) this attribute of RPM()

        For each, I generated 100,000 records of random data, approximating each
        field size with a reasonable estimate. In python 2.7, each list of 100,000
        records consumed this much ram according to the increase in the "VmData"
        field in /proc/<pid>/status:

        1) 180MB
        2) 144MB
        3) 55MB

        :return:
        """
        return tuple(getattr(self, name) for name in ['TYPE'] + list(self.UNIT_KEY_NAMES))

    @classmethod
    def from_package_info(cls, package_info):
        unit_key = {}
        metadata = {}
        for key, value in package_info.iteritems():
            if key in cls.UNIT_KEY_NAMES:
                unit_key[key] = value
            elif key == 'type' and cls != Errata:
                continue
            else:
                metadata[key] = value
        unit_key['metadata'] = metadata

        return cls(**unit_key)

    def __str__(self):
        return '%s: %s' % (self.TYPE, '-'.join(getattr(self, name) for name in self.UNIT_KEY_NAMES))


class VersionedPackage(Package):
    @property
    def key_string_without_version(self):
        keys = [getattr(self, key) for key in self.UNIT_KEY_NAMES if key not in ['epoch', 'version', 'release', 'checksum', 'checksumtype']]
        keys.append(self.TYPE)
        return '-'.join(keys)

    @property
    def complete_version(self):
        values = []
        for name in ('epoch', 'version', 'release'):
            if name in self.UNIT_KEY_NAMES:
                values.append(getattr(self, name))
        return tuple(values)

    @property
    def complete_version_serialized(self):
        return tuple(version_utils.encode(field) for field in self.complete_version)

    def __cmp__(self, other):
        return cmp(
            self.complete_version_serialized,
            other.complete_version_serialized
        )


class Distribution(Package):
    UNIT_KEY_NAMES = ('id', 'family', 'variant', 'version', 'arch')
    TYPE = 'distribution'

    def __init__(self, family, variant, version, arch):
        kwargs = locals()
        # I don't know why this is the "id", but am following the pattern of the
        # original importer
        kwargs['id'] = '-'.join(('ks', family, variant, version, arch))
        super(Distribution, self).__init__(kwargs)

    @property
    def relative_path(self):
        """
        For this model, the relative path will be a directory in which all
        related files get stored. For most unit types, this path is to one
        file.
        """
        return self.id

    def process_download_reports(self, reports):
        """
        Once downloading is complete, add information about each file to this
        model instance. This is required before saving the new unit.

        :param reports: list of successful download reports
        :type  reports: list(pulp.common.download.report.DownloadReport)
        """
        # TODO: maybe this shouldn't be in common
        metadata_files = self.metadata.setdefault('files', [])
        for report in reports:
            # the following data model is mostly intended to match what the
            # previous importer generated.
            metadata_files.append({
                'checksum': report.data['checksum'],
                'checksumtype': report.data['checksumtype'],
                'downloadurl': report.url,
                'filename': os.path.basename(report.data['relativepath']),
                'fileName': os.path.basename(report.data['relativepath']),
                'item_type': self.TYPE,
                'pkgpath': os.path.join(
                    constants.DISTRIBUTION_STORAGE_PATH,
                    self.id,
                    os.path.dirname(report.data['relativepath']),
                ),
                'relativepath': report.data['relativepath'],
                'savepath': report.destination,
                'size': report.total_bytes,
            })


class DRPM(VersionedPackage):
    UNIT_KEY_NAMES = ('epoch',  'version', 'release', 'filename', 'checksumtype', 'checksum')
    TYPE = 'drpm'

    def __init__(self, epoch, version, release, filename, checksumtype, checksum, metadata):
        Package.__init__(self, locals())

    @property
    def relative_path(self):
        return self.filename


class RPM(VersionedPackage):
    UNIT_KEY_NAMES = ('name', 'epoch', 'version', 'release', 'arch', 'checksumtype', 'checksum')
    TYPE = 'rpm'

    def __init__(self, name, epoch, version, release, arch, checksumtype, checksum, metadata):
        Package.__init__(self, locals())

    @property
    def relative_path(self):
        unit_key = self.unit_key
        return os.path.join(
            unit_key['name'], unit_key['version'], unit_key['release'],
            unit_key['arch'], unit_key['checksum'], self.metadata['relative_url_path']
        )


class Errata(Package):
    UNIT_KEY_NAMES = ('id',)
    TYPE = 'erratum'

    def __init__(self, id, metadata):
        Package.__init__(self, locals())


class PackageGroup(Package):
    UNIT_KEY_NAMES = ('id', 'repo_id')
    TYPE = 'package_group'

    def __init__(self, id, repo_id, metadata):
        Package.__init__(self, locals())


class PackageCategory(Package):
    UNIT_KEY_NAMES = ('id', 'repo_id')
    TYPE = 'package_category'

    def __init__(self, id, repo_id, metadata):
        Package.__init__(self, locals())


TYPE_MAP = {
    Distribution.TYPE: Distribution,
    DRPM.TYPE: DRPM,
    Errata.TYPE: Errata,
    PackageCategory.TYPE: PackageCategory,
    PackageGroup.TYPE: PackageGroup,
    RPM.TYPE: RPM,
}

def from_typed_unit_key_tuple(typed_tuple):
    """
    This assumes that the __init__ method takes unit key arguments in order
    followed by a dictionary for other metadata.

    :param typed_tuple:
    :return:
    """
    package_class = TYPE_MAP[typed_tuple[0]]
    args = typed_tuple[1:]
    return package_class.from_package_info(*args, metadata={})
