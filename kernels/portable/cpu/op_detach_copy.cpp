/*
 * Copyright (c) Meta Platforms, Inc. and affiliates.
 * All rights reserved.
 *
 * This source code is licensed under the BSD-style license found in the
 * LICENSE file in the root directory of this source tree.
 */

#include <executorch/runtime/kernel/kernel_includes.h>
#include <cstring>

namespace torch {
namespace executor {
namespace native {

using Tensor = exec_aten::Tensor;
using ScalarType = exec_aten::ScalarType;

namespace {} // namespace

/**
 * Copy the tener `self` to `out`, assume `self` and `out` have same type and
 * shape
 */
Tensor&
detach_copy_out(RuntimeContext& context, const Tensor& self, Tensor& out) {
  (void)context;

  torch::executor::Error err = resize_tensor(out, self.sizes());
  ET_CHECK_MSG(
      err == torch::executor::Error::Ok,
      "Failed to resize out Tensor in detach_copy_out");

  ET_CHECK_SAME_SHAPE_AND_DTYPE2(self, out);

  if (self.nbytes() > 0) {
    // Note that this check is important. It's valid for a tensor with numel 0
    // to have a null data pointer, but in some environments it's invalid to
    // pass a null pointer to memcpy() even when the size is zero.
    memcpy(out.data_ptr(), self.data_ptr(), self.nbytes());
  }

  return out;
}

} // namespace native
} // namespace executor
} // namespace torch
