#!/bin/bash

set -ex

# Ring bell on error/success exit
trap 'echo -e "\a"' EXIT

# Create pre-requisite folder layouts
script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" &>/dev/null && pwd)
base_dir=$PWD
builds_dir=$base_dir/Builds
code_dir=$base_dir/Code
mkdir --parents $builds_dir
mkdir --parents $code_dir

# Setup virtual env (absolute path to python to make sure it's the same one that uwsgi uses by default to minimise potential for error)
venv_dir=$builds_dir/session-pysogs/VEnv
/usr/bin/python3 -m venv $venv_dir
source $venv_dir/bin/activate
python3 -m pip install -r $script_dir/requirements.txt

# NOTE: oxen-encoding
if [[ ! -d $code_dir/oxen-encoding ]]; then
  git clone git@github.com:session-foundation/oxen-encoding.git $code_dir/oxen-encoding
fi
pushd $code_dir/oxen-encoding
git checkout 37f08ffb928047c102d60dec5b8d078a1d635b1c
git submodule update --init --recursive
cmake \
  -B $builds_dir/oxen-encoding/Release-Static \
  -S . \
  -D CMAKE_BUILD_TYPE=Release \
  -D BUILD_SHARED_LIBS=OFF \
  -D OXENC_BUILD_TESTS=OFF \
  -D OXENC_BUILD_DOCS=OFF \
  -D CMAKE_PREFIX_PATH=$venv_dir
cmake --build $builds_dir/oxen-encoding/Release-Static --parallel
cmake --install $builds_dir/oxen-encoding/Release-Static --prefix $venv_dir
popd

# NOTE: oxen-pyoxenc
if [[ ! -d $code_dir/oxen-pyoxenc ]]; then
  git clone git@github.com:oxen-io/oxen-pyoxenc.git $code_dir/oxen-pyoxenc
fi
pushd $code_dir/oxen-pyoxenc
rm -rf $code_dir/oxen-pyoxenc/oxenmq.egg-info $code_dir/oxen-pyoxenc/build
CC=gcc \
  CC=g++ \
  python3 -m pip install .
popd

# NOTE: libsodium
# OxenMQ links to libsodium and if we're not careful it'll pick up the system installed one which
# causes problems. First I tried linking dynamically to OxenMQ then it complained about
# zmq_timers_new, somehow liboxenmq wasn't transitively linking libzmq that it builds statically
# (maybe also polluted by system install), so we link everything statically to avoid this but now we
# rely on libzmq.a and libsodium.a so we build it ourselves to resolve the mess. Linux linking is a
# mess and hence docker was born because it's too hard.
if [[ ! -d $code_dir/libsodium ]]; then
  git clone git@github.com:jedisct1/libsodium.git $code_dir/libsodium
fi

pushd $code_dir/libsodium
git checkout 1.0.20-FINAL
git submodule update --init --recursive
./configure --prefix $venv_dir --enable-static --disable-shared --with-pic
make clean
make install -j8
popd

# NOTE: libzmq
# Install libzmq ourselves for oxenmq. Although oxenmq installs zmq for us, it doesn't install the
# libraries for us so we manually build zmq to have full control over the build process and stop
# python from complaining about a missing libzmq e.g.:
#
# ImportError: /home/fw16/Tmp/Builds/session-pysogs/VEnv/lib/python3.11/site-packages/oxenmq.cpython-311-x86_64-linux-gnu.so: undefined symbol: zmq_timers_new
if [[ ! -d $code_dir/libzmq ]]; then
  git clone git@github.com:zeromq/libzmq.git $code_dir/libzmq
fi

pushd $code_dir/libzmq
git checkout v4.3.5
# Regarding CMAKE_POLICY_VERSION_MINIMUM=3.5, if you have CMake >4.X those version treat any
# cmake script with a min value <3.5 as incompatible because they didn't want to figure out if
# the script was actually compatible with CMake 4.X or not so they introduced this cmake
# variable to assume that the script set it to 3.5 (usually scripts using such an old version,
# in this case ZMQ uses 2.8~ because they don't use many facilities of CMake in the first place)
cmake \
  -B $builds_dir/libzmq/Release-Static \
  -S . \
  -D BUILD_SHARED=ON \
  -D BUILD_STATIC=ON \
  -D CMAKE_BUILD_TYPE=Release \
  -D CMAKE_PREFIX_PATH=$venv_dir \
  -D ENABLE_CURVE=ON \
  -D ENABLE_DRAFTS=OFF \
  -D WITH_DOC=OFF \
  -D WITH_LIBSODIUM=ON \
  -D WITH_PERF_TOOL=OFF \
  -D ZMQ_BUILD_TESTS=OFF \
  -D CMAKE_POLICY_VERSION_MINIMUM=3.5
cmake --build $builds_dir/libzmq/Release-Static --parallel
cmake --install $builds_dir/libzmq/Release-Static --prefix $venv_dir
popd

# NOTE: oxen-mq
if [[ ! -d $code_dir/oxen-mq ]]; then
  git clone git@github.com:oxen-io/oxen-mq.git $code_dir/oxen-mq
fi

pushd $code_dir/oxen-mq
git checkout 9bdb79cb1aaa85c7d7c6c37ff7bc7925cdddac98
git submodule update --init --recursive
popd

# NOTE: spdlog
# We install spdlog ourselves to ensure that subsequent builds, rely on _OUR_ spdlog installation
# and stop pybind or other build systems accidentally pulling in the build from a system installed
# instance. We use the version vendored in oxen-mq so we match the exact requirement.
# NOTE: Install spdlog to ensure that oxen-pyoxenmq doesn't accidentally load spdlog installed in
# /usr/include which might be incompatible
pushd $code_dir/oxen-mq/oxen-logging/spdlog
cmake \
  -B $builds_dir/spdlog/Release-Static \
  -S . \
  -D CMAKE_BUILD_TYPE=Release \
  -D BUILD_SHARED_LIBS=OFF \
  -D SPDLOG_INSTALL=ON \
  -D SPDLOG_BUILD_PIC=ON \
  -D SPDLOG_BUILD_EXAMPLE=OFF \
  -D CMAKE_PREFIX_PATH=$venv_dir
cmake --build $builds_dir/spdlog/Release-Static --parallel
cmake --install $builds_dir/spdlog/Release-Static --prefix $venv_dir
popd

# NOTE: Now build oxen-mq after spdlog is avail in $venv_dir/include
pushd $code_dir/oxen-mq
cmake \
  -B $builds_dir/oxen-mq/Release-Shared \
  -S . \
  -D CMAKE_BUILD_TYPE=Release \
  -D BUILD_SHARED_LIBS=OFF \
  -D OXENMQ_BUILD_TESTS=OFF \
  -D OXEN_LOGGING_INSTALL=ON \
  -D USE_LTO=OFF \
  -D CMAKE_CXX_FLAGS="-I$venv_dir/include" \
  -D CMAKE_C_FLAGS="-I$venv_dir/include" \
  -D CMAKE_SHARED_LINKER_FLAGS="-L$venv_dir/lib" \
  -D CMAKE_PREFIX_PATH=$venv_dir
cmake --build $builds_dir/oxen-mq/Release-Shared --parallel
cmake --install $builds_dir/oxen-mq/Release-Shared --prefix $venv_dir
popd

# NOTE: libsession-util
if [[ ! -d $code_dir/libsession-util ]]; then
  git clone git@github.com:session-foundation/libsession-util.git $code_dir/libsession-util
fi

pushd $code_dir/libsession-util
git checkout c73fd92eb71e8108cc435fc640fbcb8faceacbb0
git submodule update --init --recursive

# This is a hack. If you are using an old cmake version that doesn't support the `ARCHIVE`
# keyword on the DESTINATION keyword then it creates an install command for the following file
# $code_dir/libsession-util/ARCHIVE so we make a dummy file just incase.
touch $code_dir/libsession-util/ARCHIVE

cmake \
  -B $builds_dir/libsession-util/Release-Static \
  -S . \
  -D CMAKE_BUILD_TYPE=Release \
  -D BUILD_SHARED_LIBS=OFF \
  -D LIBQUIC_INSTALL=ON \
  -D BUILD_STATIC_DEPS=ON \
  -D STATIC_BUNDLE=ON \
  -D USE_LTO=OFF \
  -D CMAKE_PREFIX_PATH=$venv_dir
cmake --build $builds_dir/libsession-util/Release-Static --parallel
cmake --install $builds_dir/libsession-util/Release-Static --prefix $venv_dir

# TODO: The merged library does not get installed it seems so we do it manually. It installs the
# non-merged version that is built into src/libsession-util.a with the same name confusingly.
# So we copy it in but we do one more hacky thing, we verbatim replace one of the libraries that
# libsession-python will link to, we replace the last library that it links to, to make sure it
# overwrites everything that is duplicated. Yeah hacky, but it works.
#
# The current build of libsession-util when built for libsession-python produces this error
#
#   ImportError: /home/fw16/Tmp/Builds/session-pysogs/VEnv/lib/python3.11/site-packages/session_util.cpython-311-x86_64-linux-gnu.so: undefined symbol: crypto_internal_fe25519_add
#
# This symbol exists of course, it's sitting in the libsodium-internal.a but the nature of how
# the build is setup this symbol isn't visible unless you merge the libraries into a singular .a
# file or additionally, link to this manually. But out hacky work-around above handles this.
cp -f $builds_dir/libsession-util/Release-Static/libsession-util.a $venv_dir/lib/libsession-onionreq.a

# NOTE: ngtcp2
# For similar reasons as spdlog build ngtcp2 ourselves to avoid mixing with potential sys
# installed libraries later in the libsession-python build. Libsession already builds all of
# this for us because BUILD_STATIC_DEPS=ON but dumps it into its build directory, we steal
# that and put it into our venv
cp -r $builds_dir/libsession-util/Release-Static/static-deps/* $venv_dir/
popd

# NOTE: libsession-python
if [[ ! -d $code_dir/libsession-python ]]; then
  git clone https://github.com/Doy-lee/libsession-python.git $code_dir/libsession-python
fi

pushd $code_dir/libsession-python
git checkout doyle-update-to-c73fd92
git submodule update --init --recursive
rm -rf $code_dir/libsession-python/session_util.egg-info $code_dir/libsession-python/build
CC=gcc \
  CXX=g++ \
  LDFLAGS="-L$venv_dir/lib" \
  python3 -m pip install .
popd

# NOTE: oxen-pyoxenmq
if [[ ! -d $code_dir/oxen-pyoxenmq ]]; then
  git clone git@github.com:oxen-io/oxen-pyoxenmq.git $code_dir/oxen-pyoxenmq
fi

# All the steps previously did the legwork to build everything static and install them into
# $venv_dir, static so that we have a portable build but also rebuilding everything so that
# we don't "accidentally" link to any system installed instances of the dependencies we're working
# with. Now we combine all the libraries into a single .a library so that oxen-pyoxenmq only needs
# to link to the mega library for all the symbols.
ar -M <<EOF
OPEN $venv_dir/lib/liboxenmq.a
ADDLIB $venv_dir/lib/libfmt.a
ADDLIB $venv_dir/lib/liboxen-logging.a
ADDLIB $venv_dir/lib/libsodium.a
ADDLIB $venv_dir/lib/libspdlog.a
ADDLIB $venv_dir/lib/libzmq.a
ADDLIB $venv_dir/lib/liboxenmq.a
SAVE
END
EOF

pushd $code_dir/oxen-pyoxenmq
rm -rf $code_dir/oxen-pyoxenmq/oxenmq.egg-info $code_dir/oxen-pyoxenmq/build
# Force gcc, g++ (python prefers clang if it's in the path but we want GCC/G++ to be consistent
# with installing with just build-essentials which comes with GCC)
CC=gcc \
  CC=g++ \
  LDFLAGS="-L$venv_dir/lib" \
  python3 -m pip install . --force-reinstall --no-cache-dir
popd

# NOTE: Finish!
set +ex
echo "🎉 session-pysogs portable environment setup successfully! Run the following command to get"
echo "access to a python interpreter that can run SOGS:"
echo
echo "  source $venv_dir/bin/activate"
echo
echo "Then run in the root of your session-pysogs repository to get started:"
echo
echo "  python3 -m sogs --help"
