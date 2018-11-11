from setuptools import setup, find_packages


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
          'blessings',
          'wcwidth',
      ],

      packages=find_packages('src'),
      package_dir={'': 'src'},
      entry_points={
          'console_scripts': ['borg-sya = borg_sya.cli:main'],
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
          "License :: OSI Approved :: GNU General Public License v2 or later (GPLv2+)",
          "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
          "Operating System :: Unix",
          "Programming Language :: Python",
          "Programming Language :: Python :: 3.6",
          "Programming Language :: Python :: 3.7",
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
