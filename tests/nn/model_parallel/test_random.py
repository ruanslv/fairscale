# coding=utf-8

# Copyright (c) Facebook, Inc. and its affiliates. All rights reserved.
#
# This source code is licensed under the BSD license found in the
# LICENSE file in the root directory of this source tree.

# Copyright (c) 2020, NVIDIA CORPORATION.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import torch

from fair_dev.testing.testing import dist_init, spawn_for_all_world_sizes
from fairscale.nn.model_parallel import initialize as mpu
from fairscale.nn.model_parallel import random
from fairscale.nn.model_parallel.random import get_cuda_rng_tracker, model_parallel_cuda_manual_seed


def run_test_set_cuda_rng_state(rank, model_parallel_size, filename, filename_rpc):
    dist_init(rank, model_parallel_size, filename, filename_rpc)

    if torch.distributed.get_rank() == 0:
        print("> testing set_rng_state with size {} ...".format(model_parallel_size))

    mpu.initialize_model_parallel(model_parallel_size)
    model_parallel_size = mpu.get_model_parallel_world_size()

    size = 123
    seed = 1234
    torch.cuda.manual_seed(1234)
    tensor = torch.cuda.FloatTensor(size)

    # Get the state
    rng_state = torch.cuda.get_rng_state()
    rng_state_copy = rng_state.clone()

    # Do some stuff.
    for _ in range(5):
        torch.randn(size, out=tensor)
    result_1 = tensor.clone()

    assert rng_state.sub(rng_state_copy).max() == 0
    assert torch.cuda.get_rng_state().sub(rng_state_copy).max() > 0

    # State should be different.
    new_rng_state = torch.cuda.get_rng_state()
    max_diff = new_rng_state.sub(rng_state).max()
    print(
        "   max diff in rng state (should be non-zero) on global rank {}: {}".format(
            torch.distributed.get_rank(), max_diff
        )
    )
    assert max_diff > 0

    # Reset the rng state and do the same stuff.
    random._set_cuda_rng_state(rng_state)
    for _ in range(5):
        torch.randn(size, out=tensor)
    random._set_cuda_rng_state(rng_state)
    for _ in range(5):
        torch.randn(size, out=tensor)
    result_2 = tensor.clone()

    # Results should be the same
    error = result_2.sub(result_1).abs().max()
    print(
        "   max error in generated tensors (should be zero) on global rank {}: {}".format(
            torch.distributed.get_rank(), error
        )
    )
    assert error < 1.0e-6

    # Input state should have remained intact.
    error = rng_state.sub(rng_state_copy).max()
    print(
        "   max error in rng state (should be zero) on global rank {}: {}".format(torch.distributed.get_rank(), error)
    )
    assert error == 0

    # Reset groups
    mpu.destroy_model_parallel()

    torch.distributed.barrier()
    if torch.distributed.get_rank() == 0:
        print(">> passed the test :-)")


def run_test_cuda_rng_tracker(rank, model_parallel_size, filename, filename_rpc):
    dist_init(rank, model_parallel_size, filename, filename_rpc)

    if torch.distributed.get_rank() == 0:
        print("> testing cuda rng tracker with size {} ...".format(model_parallel_size))

    mpu.initialize_model_parallel(model_parallel_size)
    model_parallel_size = mpu.get_model_parallel_world_size()

    seed_1 = 1234
    seed_2 = 4321
    size = [12, 21]
    tensor = torch.cuda.FloatTensor(size)

    # Set to seed_1 and generate two tensors.
    torch.cuda.manual_seed(seed_1)
    torch.randn(size, out=tensor)
    target_11 = tensor.clone()
    torch.randn(size, out=tensor)
    target_12 = tensor.clone()

    # Set to seed_2 and generate two tensors.
    torch.cuda.manual_seed(seed_2)
    torch.randn(size, out=tensor)
    target_21 = tensor.clone()
    torch.randn(size, out=tensor)
    target_22 = tensor.clone()

    # Now if we interleave seed_1 and seed_2,
    # we should still get the same tensors
    torch.cuda.manual_seed(seed_1)
    get_cuda_rng_tracker().add("test", seed_2)

    torch.randn(size, out=tensor)
    result_11 = tensor.clone()

    with get_cuda_rng_tracker().fork("test"):
        torch.randn(size, out=tensor)
        result_21 = tensor.clone()

    torch.randn(size, out=tensor)
    result_12 = tensor.clone()

    with get_cuda_rng_tracker().fork("test"):
        torch.randn(size, out=tensor)
        result_22 = tensor.clone()

    diff = result_11.sub(result_21).abs().max()
    diff = min(diff, result_12.sub(result_22).abs().max())
    print(
        "   max diff in generated tensors (should be non-zero) on global rank {}: {}".format(
            torch.distributed.get_rank(), diff
        )
    )
    assert diff > 1.0e-6
    error = max(result_11.sub(target_11).abs().max(), result_12.sub(target_12).abs().max())
    error = max(error, result_21.sub(target_21).abs().max())
    error = max(error, result_22.sub(target_22).abs().max())
    print(
        "   max error in generated tensors (should be zero) on global rank {}: {}".format(
            torch.distributed.get_rank(), error
        )
    )
    assert error < 1.0e-6

    # Reset the tracker
    get_cuda_rng_tracker().reset()

    # Reset groups
    mpu.destroy_model_parallel()

    torch.distributed.barrier()
    if torch.distributed.get_rank() == 0:
        print(">> passed the test :-)")


def run_test_model_parallel_cuda_manual_seed(rank, model_parallel_size, filename, filename_rpc):
    dist_init(rank, model_parallel_size, filename, filename_rpc)

    if torch.distributed.get_rank() == 0:
        print("> testing model parallel cuda manual seed with size {} ...".format(model_parallel_size))

    mpu.initialize_model_parallel(model_parallel_size)
    model_parallel_size = mpu.get_model_parallel_world_size()

    model_parallel_cuda_manual_seed(12345)
    assert torch.cuda.initial_seed() == 12345
    with get_cuda_rng_tracker().fork():
        assert torch.cuda.initial_seed() == (12345 + 2718 + mpu.get_model_parallel_rank())

    # Reset the tracker
    get_cuda_rng_tracker().reset()

    # Reset groups
    mpu.destroy_model_parallel()

    torch.distributed.barrier()
    if torch.distributed.get_rank() == 0:
        print(">> passed the test :-)")


def test_set_cuda_rng_state():
    spawn_for_all_world_sizes(run_test_set_cuda_rng_state)


def test_cuda_rng_tracker():
    spawn_for_all_world_sizes(run_test_cuda_rng_tracker)


def test_model_parallel_cuda_manual_seed():
    spawn_for_all_world_sizes(run_test_model_parallel_cuda_manual_seed)
