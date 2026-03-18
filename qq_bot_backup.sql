CREATE DATABASE IF NOT EXISTS `qq_bot` DEFAULT CHARACTER SET utf8mb4;
USE `qq_bot`;

-- 1. users 表
CREATE TABLE IF NOT EXISTS `users` (
  `user_id` varchar(20) NOT NULL,
  `nickname` varchar(50) DEFAULT NULL,
  `affinity` int(11) DEFAULT '0',
  `interaction_count` int(11) DEFAULT '0',
  `last_interaction` timestamp NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 2. chat_messages 表
CREATE TABLE IF NOT EXISTS `chat_messages` (
  `msg_id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` varchar(20) NOT NULL,
  `role` varchar(20) NOT NULL,
  `content` text NOT NULL,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`msg_id`),
  KEY `idx_user_id` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 3. user_facts 表
CREATE TABLE IF NOT EXISTS `user_facts` (
  `fact_id` int(11) NOT NULL AUTO_INCREMENT,
  `user_id` varchar(20) DEFAULT NULL,
  `fact_key` varchar(50) DEFAULT NULL,
  `fact_value` text,
  `created_at` timestamp NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`fact_id`),
  KEY `idx_user_id` (`user_id`)
  UNIQUE KEY `uk_user_fact` (`user_id`, `fact_key`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;