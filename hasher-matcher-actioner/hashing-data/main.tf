# Copyright (c) Facebook, Inc. and its affiliates. All Rights Reserved

terraform {
  required_providers {
    aws = "~> 3.0"
  }
}

provider "aws" {
  region  = var.region
  profile = var.profile
}

# S3 Bucket

resource "aws_s3_bucket" "data_bucket" {
  bucket_prefix = "${var.prefix}-hashing-data"
  acl           = "private"
  versioning {
    enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "data_bucket" {
  bucket = aws_s3_bucket.data_bucket.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Image File Notifications

resource "aws_s3_bucket_object" "images" {
  bucket       = aws_s3_bucket.data_bucket.id
  key          = "images/"
  content_type = "application/x-directory"
}

resource "aws_sns_topic" "image_notification_topic" {
  name_prefix = "${var.prefix}-images"
}

data "aws_iam_policy_document" "image_notification_topic_policy" {
  statement {
    effect    = "Allow"
    actions   = ["SNS:Publish"]
    resources = [aws_sns_topic.image_notification_topic.arn]
    principals {
      type        = "Service"
      identifiers = ["s3.amazonaws.com"]
    }
    condition {
      test     = "ArnLike"
      variable = "aws:SourceArn"
      values   = [aws_s3_bucket.data_bucket.arn]
    }
  }
}

resource "aws_sns_topic_policy" "image_notification_topic_policy" {
  arn = aws_sns_topic.image_notification_topic.arn

  policy = data.aws_iam_policy_document.image_notification_topic_policy.json
}

resource "aws_s3_bucket_notification" "image_notification" {
  bucket = aws_s3_bucket.data_bucket.id

  topic {
    topic_arn     = aws_sns_topic.image_notification_topic.arn
    events        = ["s3:ObjectCreated:*"]
    filter_prefix = "images/"
  }
}