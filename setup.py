from os import path

from setuptools import setup, find_packages

here = path.abspath(path.dirname(__file__))

with open(path.join(here, 'README.md'), encoding='utf-8') as f:
    long_description = f.read()

with open('requirements.txt') as f:
    required_packages = f.read().splitlines()

with open(path.join(here, 'VERSION')) as f:
    version = f.read().strip()

setup(
    name='smppy',
    version=version,
    license='MIT',
    description='Build SMPP server apps in Python3',
    long_description=long_description,
    long_description_content_type='text/markdown',
    keywords='smpp server',
    packages=find_packages(exclude=['contrib', 'docs', 'tests', 'scripts']),
    python_requires='>=3.7, <4',
    install_requires=required_packages,
    classifiers=[
        'Development Status :: 3 - Alpha',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Build Tools',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3.7',
    ],
    project_urls={
        'Source': 'https://github.com',
    },
)
