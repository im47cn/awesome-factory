-- 用户账户表
CREATE TABLE t_user_acct (
    id              bigint(20)     NOT NULL COMMENT '主键id',
    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',
    create_time     datetime       NOT NULL COMMENT '创建时间',
    last_updater_id varchar(36)    NOT NULL COMMENT '最后更新人id',
    last_update_time datetime      NOT NULL COMMENT '最后更新时间',
    del_flag        varchar(1)     NOT NULL DEFAULT '0' COMMENT '删除标志[0-否,1-是]',
    user_name       varchar(50)    NOT NULL COMMENT '用户名称',
    user_status     varchar(10)    NOT NULL DEFAULT 'active' COMMENT '用户状态',
    contact_phone   varchar(20)    NULL COMMENT '联系电话',
    PRIMARY KEY (id),
    UNIQUE KEY uk_user_name (user_name),
    KEY ix_user_status (user_status)
) COMMENT = '用户账户表';
