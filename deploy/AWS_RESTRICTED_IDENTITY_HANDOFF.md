# AWS Restricted Deployment Identity — Teammate Handoff Guide

## 1. Context & Objective
Per HydroPulse security requirements (SOT & deployment policy), root credentials must never be used for continuous deployment or automated tasks. The account owner must retain absolute control of billing, security, and account settings.

This guide provides the exact steps for the account owner to create a dedicated, least-privilege deployment identity (`hydropulse-deployer`) with zero billing impact.

---

## 2. Step-by-Step IAM Setup Instructions

### Step 1: Create the Restricted IAM Policy
1. **Console URL**: [https://us-east-1.console.aws.amazon.com/iam/home#/policies](https://us-east-1.console.aws.amazon.com/iam/home#/policies)
2. **Navigation Path**: IAM Dashboard → **Policies** (left sidebar) → Click **Create policy** (top right button).
3. **Policy Editor**: Click the **JSON** tab.
4. **Policy Definition**: Replace all existing JSON text with the contents of [`deploy/aws-restricted-deployer-policy.json`](file:///home/rosso/Projects/hydropulse/deploy/aws-restricted-deployer-policy.json):
```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AllowEC2ReadOps",
      "Effect": "Allow",
      "Action": [
        "ec2:DescribeInstances",
        "ec2:DescribeInstanceStatus",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcs",
        "ec2:DescribeKeyPairs",
        "ec2:DescribeImages",
        "ec2:DescribeTags"
      ],
      "Resource": "*"
    },
    {
      "Sid": "AllowInstanceLifecycleInApSouth1",
      "Effect": "Allow",
      "Action": [
        "ec2:StartInstances",
        "ec2:StopInstances",
        "ec2:RebootInstances"
      ],
      "Resource": "arn:aws:ec2:ap-south-1:697731339705:instance/*"
    },
    {
      "Sid": "AllowEC2InstanceConnectUbuntuInApSouth1",
      "Effect": "Allow",
      "Action": [
        "ec2-instance-connect:SendSSHPublicKey"
      ],
      "Resource": "arn:aws:ec2:ap-south-1:697731339705:instance/*",
      "Condition": {
        "StringEquals": {
          "ec2:osuser": "ubuntu"
        }
      }
    },
    {
      "Sid": "AllowCloudWatchMetricsRead",
      "Effect": "Allow",
      "Action": [
        "cloudwatch:GetMetricData",
        "cloudwatch:GetMetricStatistics",
        "cloudwatch:ListMetrics"
      ],
      "Resource": "*"
    },
    {
      "Sid": "DenyHighRiskAndBillingActions",
      "Effect": "Deny",
      "Action": [
        "account:*",
        "aws-portal:*",
        "billing:*",
        "ce:*",
        "cur:*",
        "organizations:*",
        "iam:Create*",
        "iam:Delete*",
        "iam:Put*",
        "iam:Attach*",
        "iam:Update*"
      ],
      "Resource": "*"
    }
  ]
}
```
5. Click **Next**.
6. **Policy Name**: `HydroPulseEC2DeployerPolicy`
7. **Description**: `Least-privilege policy for HydroPulse staging deployment in ap-south-1.`
8. Click **Create policy**.

---

### Step 2: Create the Restricted IAM User
1. **Console URL**: [https://us-east-1.console.aws.amazon.com/iam/home#/users](https://us-east-1.console.aws.amazon.com/iam/home#/users)
2. **Navigation Path**: IAM Dashboard → **Users** → Click **Create user**.
3. **User Details**:
   - **User name**: `hydropulse-deployer`
   - **Provide user access to the AWS Management Console**: Unchecked (Programmatic CLI access only).
4. Click **Next**.
5. **Set Permissions**:
   - Select **Attach policies directly**.
   - In the search bar, search for: `HydroPulseEC2DeployerPolicy`.
   - Check the checkbox next to `HydroPulseEC2DeployerPolicy`.
6. Click **Next** → Click **Create user**.

---

### Step 3: Generate Access Key for CLI
1. Click on the newly created user `hydropulse-deployer`.
2. Select the **Security credentials** tab.
3. Scroll down to **Access keys** → Click **Create access key**.
4. Use case: Select **Command Line Interface (CLI)**.
5. Check the confirmation checkbox ("I understand the above recommendation...") → Click **Next**.
6. Description tag: `HydroPulse deployment agent`.
7. Click **Create access key**.
8. Save the **Access Key ID** and **Secret Access Key** securely.
   - *Never share or paste these keys into public chat or git repositories.*
   - On the deployment machine, configure with: `aws configure --profile hydropulse-deployer`.

---

## 3. Security & Cost Guarantees

| Property | Value / Guarantee |
|:---|:---|
| **Billing Protection** | Policy explicitly **DENIES** `billing:*`, `account:*`, `ce:*` (Cost Explorer), and `organizations:*`. The deployer cannot see or modify payment methods or billing plans. |
| **IAM Protection** | Policy explicitly **DENIES** all `iam:Create*`, `iam:Delete*`, `iam:Update*`, `iam:Attach*`. The user cannot escalate privileges or create new credentials. |
| **Resource Isolation** | Operations are strictly scoped to region `ap-south-1` and instance ARN `arn:aws:ec2:ap-south-1:697731339705:instance/*`. Cannot launch unapproved GPU or expensive database instances. |
| **Financial Cost** | **$0.00**. Uses the existing `t3.micro` instance within the 12-Month AWS Free Tier allowance (750 hours/month). |
