locals {
  resource_prefix = var.prefix == "" ? "" : "${var.prefix}-"

  lambda_layers = {
    aws-nuke-layer = {
      description           = "Customer Portal - AWS Nuke executable"
      source_directory_path = "../../../src/lambda_layers/aws_nuke"
      prefix_in_zip         = null
    }
  }
}

module "lambda_layers" {
  for_each = local.lambda_layers

  # https://registry.terraform.io/modules/terraform-aws-modules/lambda/aws/latest
  source  = "terraform-aws-modules/lambda/aws"
  version = "4.9.0"

  create_layer        = true
  layer_name          = "${local.resource_prefix}${each.key}"
  description         = each.value.description
  compatible_runtimes = ["python3.9", ["python3.10"]]
  runtime             = "python"
  artifacts_dir       = "../build/lambda_layers"

  source_path = [
    {
      path = each.value.source_directory_path
      # https://registry.terraform.io/modules/terraform-aws-modules/lambda/aws/latest#combine-various-options-for-extreme-flexibility

      # pip_requirements - Controls whether to execute pip install.
      # Set to false to disable this feature, true to run pip install with requirements.txt found in path.
      # Or set to another filename which you want to use instead.
      pip_requirements = lookup(each.value, "requirements_file", false)
      prefix_in_zip    = lookup(each.value, "prefix_in_zip", "python")
      patterns         = <<END
        "!.*/.*\.txt",
        "!.*/.*\.egg-info",
        "!.*/.*\.pyc",
        "!.*/.*\.pyo",
        "!__pycache__",
        "!.*/.*\darwin.so",
        ".*",
      END
    }
  ]
}
