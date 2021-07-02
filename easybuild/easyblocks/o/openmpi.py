##
# Copyright 2019-2021 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://www.vscentrum.be),
# Flemish Research Foundation (FWO) (http://www.fwo.be/en)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# https://github.com/easybuilders/easybuild
#
# EasyBuild is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation v2.
#
# EasyBuild is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with EasyBuild.  If not, see <http://www.gnu.org/licenses/>.
##
"""
EasyBuild support for OpenMPI, implemented as an easyblock

@author: Kenneth Hoste (Ghent University)
"""
import os
import re
from distutils.version import LooseVersion

from easybuild.easyblocks.generic.configuremake import ConfigureMake
from easybuild.framework.easyconfig.constants import EASYCONFIG_CONSTANTS
from easybuild.tools.build_log import EasyBuildError
from easybuild.tools.modules import get_software_root
from easybuild.tools.systemtools import check_os_dependency, get_shared_lib_ext


class EB_OpenMPI(ConfigureMake):
    """OpenMPI easyblock."""

    def configure_step(self):
        """Custom configuration step for OpenMPI."""

        def config_opt_used(key, enable_opt=False):
            """Helper function to check whether a configure option is already specified in 'configopts'."""
            if enable_opt:
                regex = '--(disable|enable)-%s' % key
            else:
                regex = '--(with|without)-%s' % key

            return bool(re.search(regex, self.cfg['configopts']))

        config_opt_names = [
            # suppress failure modes in relation to mpirun path
            'mpirun-prefix-by-default',
            # build shared libraries
            'shared',
        ]

        for key in config_opt_names:
            if not config_opt_used(key, enable_opt=True):
                self.cfg.update('configopts', '--enable-%s' % key)

        # List of EasyBuild dependencies for which OMPI has known options
        known_dependencies = ('CUDA', 'hwloc', 'libevent', 'libfabric', 'PMIx', 'UCX')
        # Value to use for `--with-<dep>=<value>` if the dependency is not specified in the easyconfig
        # No entry is interpreted as no option added at all
        # This is to make builds reproducible even when the system libraries are changed and avoids failures
        # due to e.g. finding only PMIx but not libevent on the system
        unused_dep_value = dict()
        # Known options since version 3.0 (no earlier ones checked)
        if LooseVersion(self.version) >= LooseVersion('3.0'):
            # Default to disable the option with "no"
            unused_dep_value = {dep: 'no' for dep in known_dependencies}
            # For these the default is to use an internal copy and not using any is not supported
            for dep in ('hwloc', 'libevent', 'PMIx'):
                unused_dep_value[dep] = 'internal'

        # handle dependencies
        for dep in known_dependencies:
            opt_name = dep.lower()
            # If the option is already used, don't add it
            if config_opt_used(opt_name):
                continue

            # libfabric option renamed in OpenMPI 3.1.0 to ofi
            if dep == 'libfabric' and LooseVersion(self.version) >= LooseVersion('3.1'):
                opt_name = 'ofi'
                # Check new option name. They are synonyms since 3.1.0 for backward compatibility
                if config_opt_used(opt_name):
                    continue

            dep_root = get_software_root(dep)
            # If the dependency is loaded, specify its path, else use the "unused" value, if any
            if dep_root:
                opt_value = dep_root
            else:
                opt_value = unused_dep_value.get(dep)
            if opt_value is not None:
                self.cfg.update('configopts', '--with-%s=%s' % (opt_name, opt_value))

        if bool(get_software_root('PMIx')) != bool(get_software_root('libevent')):
            raise EasyBuildError('You must either use both PMIx and libevent as dependencies or none of them. '
                                 'This is to enforce the same libevent is used for OpenMPI as for PMIx or '
                                 'the behavior may be unpredictable.')

        # check whether VERBS support should be enabled
        if not config_opt_used('verbs'):

            # for OpenMPI v4.x, the openib BTL should be disabled when UCX is used;
            # this is required to avoid "error initializing an OpenFabrics device" warnings,
            # see also https://www.open-mpi.org/faq/?category=all#ofa-device-error
            if LooseVersion(self.version) >= LooseVersion('4.0.0') and '--with-ucx' in self.cfg['configopts']:
                verbs = False
            else:
                # auto-detect based on available OS packages
                os_packages = EASYCONFIG_CONSTANTS['OS_PKG_IBVERBS_DEV'][0]
                verbs = any(check_os_dependency(osdep) for osdep in os_packages)

            if verbs:
                self.cfg.update('configopts', '--with-verbs')
            else:
                self.cfg.update('configopts', '--without-verbs')

        super(EB_OpenMPI, self).configure_step()

    def sanity_check_step(self):
        """Custom sanity check for OpenMPI."""

        bin_names = ['mpicc', 'mpicxx', 'mpif90', 'mpifort', 'mpirun', 'ompi_info', 'opal_wrapper', 'orterun']
        bin_files = [os.path.join('bin', x) for x in bin_names]

        shlib_ext = get_shared_lib_ext()
        lib_names = ['mpi_mpifh', 'mpi', 'ompitrace', 'open-pal', 'open-rte']
        lib_files = [os.path.join('lib', 'lib%s.%s' % (x, shlib_ext)) for x in lib_names]

        inc_names = ['mpi-ext', 'mpif-config', 'mpif', 'mpi', 'mpi_portable_platform']
        inc_files = [os.path.join('include', x + '.h') for x in inc_names]

        custom_paths = {
            'files': bin_files + inc_files + lib_files,
            'dirs': [],
        }

        # make sure MPI compiler wrappers pick up correct compilers
        expected = {
            'mpicc': os.getenv('CC', 'gcc'),
            'mpicxx': os.getenv('CXX', 'g++'),
            'mpifort': os.getenv('FC', 'gfortran'),
            'mpif90': os.getenv('F90', 'gfortran'),
        }
        # actual pattern for gfortran is "GNU Fortran"
        for key in ['mpifort', 'mpif90']:
            if expected[key] == 'gfortran':
                expected[key] = "GNU Fortran"
        # for PGI, correct pattern is "pgfortran" with mpif90
        if expected['mpif90'] == 'pgf90':
            expected['mpif90'] = 'pgfortran'

        custom_commands = ["%s --version | grep '%s'" % (key, expected[key]) for key in sorted(expected.keys())]

        super(EB_OpenMPI, self).sanity_check_step(custom_paths=custom_paths, custom_commands=custom_commands)
