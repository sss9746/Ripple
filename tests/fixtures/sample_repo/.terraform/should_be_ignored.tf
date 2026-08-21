resource "null_resource" "ignored" {
  triggers = {
    reason = "This file is inside .terraform and must not be indexed."
  }
}
