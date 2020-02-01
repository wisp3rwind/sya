from setuptools import setup, find_packages
import subprocess


# Compile gresource for GUI application
subprocess.call(
    ["glib-compile-resources", "--generate", "sya.gresource.xml"],
    cwd="src/borg_sya/data",
)


setup(name='borg_sya',
      author='wisp3rwind',
      author_email='',
      description='',
      long_description='',  # TODO: read README, CHANGELOG
      url='https://github.com/wisp3rwind/sya',

      use_scm_version=True,
      setup_requires=['setuptools_scm'],

      install_requires=[
          'borgbackup',
          'click',
          'pyyaml',
          'wcwidth',
          'blessings',
          'pygobject',
      ],
      extras_require={
          "CLI": [
              # 'blessings',
          ],
          "GUI": [
              # 'pygobject',
          ],
      },

      packages=find_packages('src'),
      package_dir={'': 'src'},
      entry_points={
          'console_scripts': [
              'borg-sya = borg_sya.cli:main [CLI]',
          ],
          # 'gui_scripts': [
          #     'borg-sya-gui = borg_sya.gui:main [GUI]',
          # ],
      },
      package_data={
          "borg_sya": ["data/*.gresource"]
      },

      # List of classifiers: http://pypi.python.org/pypi?%3Aaction=list_classifiers
      classifiers=[
          "Development Status :: 3 - Alpha",
          # "Development Status :: 4 - Beta",
          # "Development Status :: 5 - Production/Stable",
          # "Development Status :: 6 - Mature",
          # "Development Status :: 7 - Inactive",
          "Environment :: Console",
          "Environment :: X11 Applications :: GTK",
          "Intended Audience :: End Users/Desktop",
          "Intended Audience :: System Administrators",
          "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
          "Operating System :: Unix",
          "Programming Language :: Python",
          # We use f-strings, which are 3.6+
          # We use importlib, which is 3.7+
          "Programming Language :: Python :: 3.7",
          "Programming Language :: Python :: 3.8",
          "Programming Language :: Python :: Implementation",
          "Programming Language :: Python :: Implementation :: CPython",
          # not tested:
          # "Programming Language :: Python :: Implementation :: IronPython",
          # "Programming Language :: Python :: Implementation :: Jython",
          # "Programming Language :: Python :: Implementation :: MicroPython",
          # "Programming Language :: Python :: Implementation :: PyPy",
          # "Programming Language :: Python :: Implementation :: Stackless",
          "Topic :: System",
          "Topic :: System :: Archiving",
          "Topic :: System :: Archiving :: Backup",
            ],
      )

# vim: set et sw=4 :
