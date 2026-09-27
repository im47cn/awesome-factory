-- 操作日志表
CREATE TABLE t_operator_log (
    id              bigint(20)     NOT NULL COMMENT '主键id',
    operator_type   varchar(20)    NOT NULL COMMENT '操作类型',
    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',
    create_time     datetime       NOT NULL COMMENT '创建时间',
    del_flag        tinyint(4)     NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',
    operator_status varchar(20)    NOT NULL COMMENT '操作状态',
    operator_id     bigint(20)     NOT NULL COMMENT '操作人id',
    PRIMARY KEY (id),
    UNIQUE KEY uk_creator_id (creator_id),
    KEY ix_operator_status (operator_status)
) COMMENT = '操作日志表';
