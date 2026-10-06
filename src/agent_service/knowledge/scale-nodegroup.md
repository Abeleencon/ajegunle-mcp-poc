# Scale the EKS node group

Tags: nodes, capacity, scale, node group, pending pods

## When
Pods stay `Pending` with `Insufficient cpu` events.

## Steps
1. Raise `node_desired_size` (and `node_max_size` if needed) in `terraform.tfvars`.
2. `terraform plan` and review that only `aws_eks_node_group.default` changes.
3. `terraform apply` through the reviewed pipeline, never from a laptop.

The default node group is 2 x m6i.large across private subnets, with a maximum of 4 nodes.
