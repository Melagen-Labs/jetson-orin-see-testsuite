cmake_minimum_required(VERSION 3.18)
project(cuda_particles LANGUAGES CXX CUDA)

# Headless deterministic CUDA particle workload for Jetson Orin Nano SEE testing.
# No OpenGL: PARTICLES_USE_GL is intentionally never defined, so every GL path in
# the vendored NVIDIA sample compiles out and the CUDA-only branches are used.

set(CMAKE_CXX_STANDARD 14)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

# Jetson Orin Nano = Ampere, compute capability 8.7.
if(NOT DEFINED CMAKE_CUDA_ARCHITECTURES)
  set(CMAKE_CUDA_ARCHITECTURES 87)
endif()

# Pulls in the CUDA toolkit headers (cuda_runtime.h, vector_types.h, ...) and the
# runtime lib. Needed for the HOST-compiled .cpp files — nvcc finds these on its
# own for .cu files, but the C++ compiler does not.
find_package(CUDAToolkit REQUIRED)

set(SRC_DIR   ${CMAKE_CURRENT_SOURCE_DIR}/src)
set(NV_COMMON ${CMAKE_CURRENT_SOURCE_DIR}/third_party/nvidia_common)

add_executable(cuda_particles
  particles_main.cpp
  config.cpp
  checksum.cpp
  logger.cpp
  ${SRC_DIR}/particleSystem.cpp
  ${SRC_DIR}/particleSystem_cuda.cu
)

target_include_directories(cuda_particles PRIVATE
  ${CMAKE_CURRENT_SOURCE_DIR}
  ${SRC_DIR}
  ${NV_COMMON}
)

# CUDA::cudart propagates the toolkit include dir to the .cpp files and links the
# CUDA runtime (cudaMalloc/cudaMemcpy/cudaSetDevice used in the host code).
target_link_libraries(cuda_particles PRIVATE CUDA::cudart)

set_target_properties(cuda_particles PROPERTIES
  CUDA_SEPARABLE_COMPILATION OFF
)

# Uncomment to build the (unused) OpenGL rendering path instead of headless.
# target_compile_definitions(cuda_particles PRIVATE PARTICLES_USE_GL)
