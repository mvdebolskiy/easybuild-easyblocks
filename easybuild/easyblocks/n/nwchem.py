##
# Copyright 2009-2013 Ghent University
#
# This file is part of EasyBuild,
# originally created by the HPC team of Ghent University (http://ugent.be/hpc/en),
# with support of Ghent University (http://ugent.be/hpc),
# the Flemish Supercomputer Centre (VSC) (https://vscentrum.be/nl/en),
# the Hercules foundation (http://www.herculesstichting.be/in_English)
# and the Department of Economy, Science and Innovation (EWI) (http://www.ewi-vlaanderen.be/en).
#
# http://github.com/hpcugent/easybuild
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
EasyBuild support for building and installing NWChem, implemented as an easyblock

@author: Kenneth Hoste (Ghent University)
"""
import os
import re
import shutil
import tempfile

import easybuild.tools.config as config
import easybuild.tools.environment as env
import easybuild.tools.toolchain as toolchain
from easybuild.easyblocks.generic.configuremake import ConfigureMake
from easybuild.framework.easyconfig import CUSTOM
from easybuild.tools.filetools import mkdir, run_cmd
from easybuild.tools.modules import get_software_root, get_software_version


class EB_NWChem(ConfigureMake):
    """Support for building/installing NWChem."""

    def __init__(self, *args, **kwargs):
        """Initialisation of custom class variables for NWChem."""
        super(EB_NWChem, self).__init__(*args, **kwargs)

        self.test_cases_dir = None

    @staticmethod
    def extra_options():
        """Custom easyconfig parameters for NWChem."""

        extra_vars = [
                      ('target', ["LINUX64", "Target platform", CUSTOM]),
                      # possible options for ARMCI_NETWORK on LINUX64 with Infiniband:
                      # OPENIB, MPI-MT, MPI-SPAWN, MELLANOX
                      ('armci_network', ["OPENIB", "Network protocol to use", CUSTOM]),
                      ('msg_comms', ["MPI", "Type of message communication", CUSTOM]),
                      ('modules', ["all", "NWChem modules to build", CUSTOM]),
                      ('lib_defines', ["", "Additional defines for C preprocessor", CUSTOM]),
                      ('tests', [True, "Run example test cases", CUSTOM]),
                      # lots of tests fail, so allow a certain fail ratio
                      ('max_fail_ratio', [0.5, "Maximum test case fail ratio", CUSTOM])
                     ]
        return ConfigureMake.extra_options(extra_vars)

    def setvar_env_makeopt(self, name, value):
        """Set a variable both in the environment and a an option to make."""
        env.setvar(name, value)
        self.cfg.update('makeopts', "%s='%s'" % (name, value))

    def configure_step(self):
        """Custom configuration procedure for NWChem."""

        # building NWChem in a long path name is an issue, so let's make sure we have a short one
        try:
            # NWChem insists that version is in name of build dir
            tmpdir = tempfile.mkdtemp(suffix=self.version)
            os.rmdir(tmpdir)
            os.symlink(self.cfg['start_dir'], tmpdir)
            os.chdir(tmpdir)
            self.cfg['start_dir'] = tmpdir
        except OSError, err:
            self.log.error("Failed to symlink build dir to a shorter path name: %s" % err)

        # change to actual build dir
        try:
            os.chdir('src')
        except OSError, err:
            self.log.error("Failed to change to build dir: %s" % err)

        nwchem_modules = self.cfg['modules']

        # set required NWChem environment variables
        env.setvar('NWCHEM_TOP', self.cfg['start_dir'])
        env.setvar('NWCHEM_TARGET', self.cfg['target'])
        env.setvar('MSG_COMMS', self.cfg['msg_comms'])
        env.setvar('ARMCI_NETWORK', self.cfg['armci_network'])
        if self.cfg['armci_network'] in ["OPENIB"]:
            env.setvar('IB_INCLUDE', "/usr/include")
            env.setvar('IB_LIB', "/usr/lib64")
            env.setvar('IB_LIB_NAME', "-libumad -libverbs -lpthread")

        if 'python' in self.cfg['modules']:
            python_root = get_software_root('Python')
            if not python_root:
                self.log.error("Python module not loaded, you should add Python as a dependency.")
            env.setvar('PYTHONHOME', python_root)
            pyver = '.'.join(get_software_version('Python').split('.')[0:2])
            env.setvar('PYTHONVERSION', pyver)

        env.setvar('LARGE_FILES', 'TRUE')
        env.setvar('USE_NOFSCHECK', 'TRUE')

        for var in ['USE_MPI', 'USE_MPIF', 'USE_MPIF4']:
            env.setvar(var, 'y')
        for var in ['CC', 'CXX', 'F90']:
            env.setvar('MPI_%s' % var, os.getenv('MPI%s' % var))
        env.setvar('MPI_LOC', os.path.dirname(os.getenv('MPI_INC_DIR')))
        env.setvar('MPI_LIB', os.getenv('MPI_LIB_DIR'))
        env.setvar('MPI_INCLUDE', os.getenv('MPI_INC_DIR'))
        libmpi = None
        mpi_family = self.toolchain.mpi_family()
        if mpi_family in toolchain.OPENMPI:
            libmpi = "-lmpi_f90 -lmpi_f77 -lmpi -ldl -Wl,--export-dynamic -lnsl -lutil"
        elif mpi_family in [toolchain.INTELMPI]:
            if self.cfg['armci_network'] in ["MPI-MT"]:
                libmpi = "-lmpigf -lmpigi -lmpi_ilp64 -lmpi_mt"
            else:
                libmpi = "-lmpigf -lmpigi -lmpi_ilp64 -lmpi"
        elif mpi_family in [toolchain.MPICH2]:
            libmpi = "-lmpich -lopa -lmpl -lrt -lpthread"
        else:
            self.log.error("Don't know how to set LIBMPI for %s" % mpi_family)
        env.setvar('LIBMPI', libmpi)

        # compiler optimization flags: set environment variables _and_ add them to list of make options
        self.setvar_env_makeopt('COPTIMIZE', os.getenv('CFLAGS'))
        self.setvar_env_makeopt('FOPTIMIZE', os.getenv('FFLAGS'))

        # BLAS and ScaLAPACK
        self.setvar_env_makeopt('BLASOPT', '%s -L%s %s %s' % (os.getenv('LDFLAGS'), os.getenv('MPI_LIB_DIR'),
                                                              os.getenv('LIBSCALAPACK_MT'), libmpi))

        self.setvar_env_makeopt('SCALAPACK', '%s %s' % (os.getenv('LDFLAGS'), os.getenv('LIBSCALAPACK_MT')))
        if self.toolchain.options['i8']:
            size = 8
            self.setvar_env_makeopt('USE_SCALAPACK_I8', 'y')
            self.cfg.update('lib_defines', '-DSCALAPACK_I8')
        else:
            self.setvar_env_makeopt('HAS_BLAS', 'yes')
            self.setvar_env_makeopt('USE_SCALAPACK', 'y')
            size = 4

        # set sizes
        for lib in ['BLAS', 'LAPACK', 'SCALAPACK']:
            self.setvar_env_makeopt('%s_SIZE' % lib, str(size))

        env.setvar('NWCHEM_MODULES', nwchem_modules)

        env.setvar('LIB_DEFINES', self.cfg['lib_defines'])

        # clean first (why not)
        run_cmd("make clean", simple=True, log_all=True, log_ok=True)

        # configure build
        cmd = "make %s nwchem_config" % self.cfg['makeopts']
        run_cmd(cmd, simple=True, log_all=True, log_ok=True, log_output=True)

    def build_step(self):
        """Custom build procedure for NWChem."""

        # set FC
        self.setvar_env_makeopt('FC', os.getenv('F77'))

        # check whether 64-bit integers should be used, and act on it
        if not self.toolchain.options['i8']:
            if self.cfg['parallel']:
                self.cfg.update('makeopts', '-j %s' % self.cfg['parallel'])
            run_cmd("make %s 64_to_32" % self.cfg['makeopts'], simple=True, log_all=True, log_ok=True, log_output=True)

            self.setvar_env_makeopt('USE_64TO32', "y")

        # unset env vars that cause trouble during NWChem build or cause build to generate incorrect stuff
        for var in ['CFLAGS', 'FFLAGS', 'LIBS']:
            val = os.getenv(var)
            if val:
                self.log.info("%s was defined as '%s', need to unset it to avoid problems..." % (var, val))
            os.unsetenv(var)
            os.environ.pop(var)

        super(EB_NWChem, self).build_step(verbose=True)

        # build version info
        try:
            self.log.info("Building version info...")

            cwd = os.getcwd()
            os.chdir(os.path.join(self.cfg['start_dir'], 'src', 'util'))

            run_cmd("make version", simple=True, log_all=True, log_ok=True, log_output=True)
            run_cmd("make", simple=True, log_all=True, log_ok=True, log_output=True)

            os.chdir(os.path.join(self.cfg['start_dir'], 'src'))
            run_cmd("make link", simple=True, log_all=True, log_ok=True, log_output=True)

            os.chdir(cwd)

        except OSError, err:
            self.log.error("Failed to build version info: %s" % err)

        # run getmem.nwchem script to assess memory availability and make an educated guess
        # this is an alternative to specifying -DDFLT_TOT_MEM via LIB_DEFINES
        # this recompiles the appropriate files and relinks
        if not 'DDFLT_TOT_MEM' in self.cfg['lib_defines']:
            try:
                os.chdir(os.path.join(self.cfg['start_dir'], 'contrib'))
                run_cmd("./getmem.nwchem", simple=True, log_all=True, log_ok=True, log_output=True)
                os.chdir(self.cfg['start_dir'])
            except OSError, err:
                self.log.error("Failed to run getmem.nwchem script: %s" % err)

    def install_step(self):
        """Custom install procedure for NWChem."""

        try:
            # binary
            bindir = os.path.join(self.installdir, 'bin')
            mkdir(bindir)
            shutil.copy(os.path.join(self.cfg['start_dir'], 'bin', self.cfg['target'], 'nwchem'),
                        bindir)

            # data
            shutil.copytree(os.path.join(self.cfg['start_dir'], 'src', 'data'),
                            os.path.join(self.installdir, 'data'))
            shutil.copytree(os.path.join(self.cfg['start_dir'], 'src', 'basis', 'libraries'),
                            os.path.join(self.installdir, 'data', 'libraries'))
            shutil.copytree(os.path.join(self.cfg['start_dir'], 'src', 'nwpw', 'libraryps'),
                            os.path.join(self.installdir, 'data', 'libraryps'))

        except OSError, err:
            self.log.error("Failed to install NWChem: %s" % err)

        # create NWChem settings file
        fn = os.path.join(self.installdir, 'data', 'default.nwchemrc')
        txt = '\n'.join([
                         "nwchem_basis_library %(path)s/data/libraries/",
                         "nwchem_nwpw_library %(path)s/data/libraryps/",
                         "ffield amber",
                         "amber_1 %(path)s/data/amber_s/",
                         "amber_2 %(path)s/data/amber_q/",
                         "amber_3 %(path)s/data/amber_x/",
                         "amber_4 %(path)s/data/amber_u/",
                         "spce %(path)s/data/solvents/spce.rst",
                         "charmm_s %(path)s/data/charmm_s/",
                         "charmm_x %(path)s/data/charmm_x/",
                        ]) % {'path': self.installdir}

        try:
            f = open(fn, 'w')
            f.write(txt)
            f.close()
        except IOError, err:
            self.log.error("Failed to create %s: %s" % (fn, err))

    def sanity_check_step(self):
        """Custom sanity check for NWChem."""

        custom_paths = {
                        'files': ['bin/nwchem'],
                        'dirs': [os.path.join('data', x) for x in ['amber_q', 'amber_s', 'amber_t',
                                                                   'amber_u', 'amber_x', 'charmm_s',
                                                                   'charmm_x', 'solvents',
                                                                   'libraries', 'libraryps']],
                       }

        super(EB_NWChem, self).sanity_check_step(custom_paths=custom_paths)

    def make_module_extra(self):
        """Custom extra module file entries for NWChem."""

        txt = super(EB_NWChem, self).make_module_extra()

        txt += self.moduleGenerator.set_environment("PYTHONHOME", get_software_root('Python'))
        # '/' at the end is critical for NWCHEM_BASIS_LIBRARY!
        txt += self.moduleGenerator.set_environment('NWCHEM_BASIS_LIBRARY', "$root/data/libraries/")

        return txt

    def cleanup_step(self):
        """Copy stuff from build directory we still need, if any."""

        try:
            exs_dir = os.path.join(self.cfg['start_dir'], 'examples')

            self.examples_dir = os.path.join(tempfile.mkdtemp(), 'examples')

            shutil.copytree(exs_dir, self.examples_dir)

            self.log.info("Copied %s to %s." % (exs_dir, self.examples_dir))

        except OSError, err:
            self.log.error("Failed to copy examples: %s" % err)

        super(EB_NWChem, self).cleanup_step()

    def test_cases_step(self):
        """Run provided list of test cases, or provided examples is no test cases were specified."""

        # run all examples if no test cases were specified
        # order and grouping is important for some of these tests (e.g., [o]h3tr*
        if type(self.cfg['tests']) is bool:
            examples = [('qmd', ['3carbo_dft.nw', '3carbo.nw', 'h2o_scf.nw']),
                        ('pspw', ['C2.nw', 'C6.nw', 'CG.nw', 'Carbene.nw', 'Na16.nw', 'NaCl.nw']),
                        ('tcepolar', ['ccsdt_polar_small.nw', 'ccsd_polar_big.nw', 'ccsd_polar_small.nw']),
                        ('dirdyvtst/h3', ['h3tr1.nw', 'h3tr2.nw', 'h3tr3.nw', 'h3tr4.nw', 'h3tr5.nw']),
                        ('dirdyvtst/oh3', ['oh3tr1.nw', 'oh3tr2.nw', 'oh3tr3.nw', 'oh3tr4.nw', 'oh3tr5.nw']),
                        ('pspw/session1', ['band.nw', 'si4.linear.nw', 'si4.rhombus.nw', 'S2-drift.nw', 'diamond.nw',
                                           'silicon.nw', 'S2.nw', 'si4.rectangle.nw']),
                        ('pspw/MgO+Cu', ['pspw_MgO.nw']), ('pspw/C2H6', ['C2H6.nw']), ('pspw/Carbene', ['triplet.nw']),
                        ('md/dna', ['dna.nw']), ('md/ache', ['mache.nw']), ('md/myo', ['myo.nw']),
                        ('md/nak', ['NaK.nw']), ('md/nak', ['18c6NaK.nw']), ('md/membrane', ['membrane.nw']),
                        ('md/sdm', ['sdm.nw']), ('md/crown', ['crown.nw']), ('md/hrc', ['hrc.nw']),
                        ('md/benzene', ['benzene.nw'])]
            self.cfg['tests'] = [(os.path.join(self.examples_dir, d), l) for (d, l) in examples]
            self.log.info("List of examples to be run as test cases: %s" % self.cfg['tests'])

        try:
            # symlink $HOME/.nwchemrc to local copy of default nwchemrc
            default_nwchemrc = os.path.join(self.installdir, 'data', 'default.nwchemrc')
            home_nwchemrc = os.path.join(os.getenv('HOME'), '.nwchemrc')
            local_nwchemrc = os.path.join(tempfile.gettempdir(), os.getenv('USER'), 'easybuild_nwchem', '.nwchemrc')

            local_nwchemrc_dir = os.path.dirname(local_nwchemrc)
            if not os.path.exists(local_nwchemrc_dir):
                os.makedirs(local_nwchemrc_dir)

            shutil.copy2(default_nwchemrc, local_nwchemrc)
            symlink_ok = False
            if os.path.exists(home_nwchemrc):
                if not os.path.islink(home_nwchemrc) or not os.path.samefile(home_nwchemrc, local_nwchemrc):
                    self.log.error("File %s is present, but is not a symlink to %s" % (home_nwchemrc, local_nwchemrc))
                else:
                    symlink_ok = True

            if not symlink_ok:
                os.symlink(local_nwchemrc, home_nwchemrc)

            # run tests, keep track of fail ratio
            cwd = os.getcwd()

            fail = 0.0
            tot = 0.0

            success_regexp = re.compile("Total times\s*cpu:.*wall:.*")

            test_cases_logfn = os.path.join(self.installdir, config.log_path(), 'test_cases.log')
            test_cases_log = open(test_cases_logfn, "w")

            for (testdir, tests) in self.cfg['tests']:

                # run test in a temporary dir
                tmpdir = tempfile.mkdtemp(prefix='nwchem_test_')
                os.chdir(tmpdir)

                # copy all files in test case dir
                for item in os.listdir(testdir):
                    test_file = os.path.join(testdir, item)
                    if os.path.isfile(test_file):
                        self.log.debug("Copying %s to %s" % (test_file, tmpdir))
                        shutil.copy2(test_file, tmpdir)

                # run tests
                for testx in tests:
                    cmd = "nwchem %s" % testx
                    msg = "Running test '%s' (from %s) in %s..." % (cmd, testdir, tmpdir)
                    self.log.info(msg)
                    test_cases_log.write("\n%s\n" % msg)
                    (out, ec) = run_cmd(cmd, simple=False, log_all=False, log_ok=False, log_output=True)

                    # check exit code and output
                    if ec:
                        msg = "Test %s failed (exit code: %s)!" % (testx, ec)
                        self.log.warning(msg)
                        test_cases_log.write('FAIL: %s' % msg)
                        fail += 1
                    else:
                        if success_regexp.search(out):
                            msg = "Test %s successful!" % testx
                            self.log.info(msg)
                            test_cases_log.write('SUCCESS: %s' % msg)
                        else:
                            msg = "No 'Total times' found for test %s (but exit code is %s)!" % (testx, ec)
                            self.log.warning(msg)
                            test_cases_log.write('FAIL: %s' % msg)
                            fail += 1

                    test_cases_log.write("\nOUTPUT:\n\n%s\n\n" % out)

                    tot += 1

                # go back
                os.chdir(cwd)
                shutil.rmtree(tmpdir)

            fail_ratio = fail / tot
            fail_pcnt = fail_ratio * 100

            msg = "%d of %d tests failed (%s%%)!" % (fail, tot, fail_pcnt)
            self.log.info(msg)
            test_cases_log.write('\n\nSUMMARY: %s' % msg)

            test_cases_log.close()
            self.log.info("Log for test cases saved at %s" % test_cases_logfn)

            if fail_ratio > self.cfg['max_fail_ratio']:
                max_fail_pcnt = self.cfg['max_fail_ratio'] * 100
                self.log.error("Over %s%% of test cases failed, assuming broken build." % max_fail_pcnt)

            shutil.rmtree(self.examples_dir)
            shutil.rmtree(local_nwchemrc_dir)

        except OSError, err:
            self.log.error("Failed to run test cases: %s" % err)