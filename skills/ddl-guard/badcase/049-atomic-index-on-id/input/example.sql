-- 订单信息表
CREATE TABLE t_order_info (
    id              bigint(20)     NOT NULL COMMENT '主键id',
    order_no        varchar(36)    NOT NULL COMMENT '订单编号',
    creator_id      varchar(36)    NOT NULL COMMENT '创建人id',
    create_time     datetime       NOT NULL COMMENT '创建时间',
    last_updater_id varchar(36)    NOT NULL COMMENT '最后更新人id',
    last_update_time datetime      NOT NULL COMMENT '最后更新时间',
    del_flag        tinyint(4)     NOT NULL DEFAULT 0 COMMENT '删除标志[0-否,1-是]',
    order_status    varchar(10)    NOT NULL COMMENT '订单状态',
    buyer_id        bigint(20)     NOT NULL COMMENT '买家id',
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no),
    KEY ix_order_status (order_status),
    KEY ix_id (id)
) COMMENT = '订单信息表';
