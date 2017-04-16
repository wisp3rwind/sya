from setuptools import setup


setup(name='borg_sya',
      author='',
      author_email='',
      description='',

      use_scm_version=True,
      setup_requires=['setuptools_scm'],

      install_requires=['borgbackup'],

      packages=['borg_sya'],
      entry_points={
          'console_scripts': ['borg-sya = borg_sya:main']
          },
      )
