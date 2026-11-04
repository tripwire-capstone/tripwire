resource "aws_db_subnet_group" "main" {
  name       = var.name
  subnet_ids = values(aws_subnet.private)[*].id

  tags = {
    Name = var.name
  }
}

# Final snapshot names must be unique, and a new database gets a new suffix
resource "random_id" "db_final_snapshot" {
  byte_length = 3
}

resource "aws_db_instance" "main" {
  identifier     = "${var.name}-db"
  engine         = "postgres"
  engine_version = "16"
  instance_class = "db.t4g.micro"

  allocated_storage     = 20
  max_allocated_storage = 50
  storage_type          = "gp3"
  storage_encrypted     = true

  db_name  = "laminar"
  username = "lmnr_admin"
  password = random_password.db.result

  # Same AZ as the data box, so the stack depends on one AZ, not two
  availability_zone      = local.azs[0]
  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]
  publicly_accessible    = false
  multi_az               = false

  backup_retention_period    = var.db_backup_retention_days
  auto_minor_version_upgrade = true
  copy_tags_to_snapshot      = true

  deletion_protection       = false
  skip_final_snapshot       = !var.keep_final_snapshots
  final_snapshot_identifier = "${var.name}-db-final-${random_id.db_final_snapshot.hex}"
  snapshot_identifier       = var.db_snapshot_identifier

  tags = {
    Name = "${var.name}-db"
  }

  # Changing the snapshot later would replace a live database with the old copy
  lifecycle {
    ignore_changes = [snapshot_identifier]
  }
}