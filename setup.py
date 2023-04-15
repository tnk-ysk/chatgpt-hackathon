import os
from setuptools import setup, find_packages

NAME = 'prassi'

setup_dir = os.path.abspath(os.path.dirname(__file__))
ver = {}
with open(os.path.join(setup_dir, NAME, '__version__.py')) as f:
    exec(f.read(), ver)


def _requires_from_file(filename):
    return open(filename).read().splitlines()


setup(
    name=NAME,
    description='git pr assistant',
    version=ver.get('__version__'),
    packages=find_packages(),
    python_requires='>=3.7',
    install_requires=_requires_from_file('requirements.txt'),
    entry_points={
        'console_scripts': ['prassi = prassi.main:main']
    },
)
