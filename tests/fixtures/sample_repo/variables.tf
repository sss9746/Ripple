variable "region" {
  type    = string
  default = "us-west-2"
}

variable "environment" {
  type    = string
  default = "test"
}

locals {
  name_prefix = "${var.environment}-${var.region}"
}
