-- 订单信息表（缺 del_flag）
CREATE TABLE t_order_del_probe (
    id               bigint(20)     NOT NULL COMMENT '主键id',
    order_no         varchar(36)    NOT NULL COMMENT '订单编号',
    creator_id       varchar(36)    NOT NULL DEFAULT '' COMMENT '创建人id',
    create_time      datetime       NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    last_updater_id  varchar(36)    NOT NULL DEFAULT '' COMMENT '最后更新人id',
    last_update_time datetime       NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '最后更新时间',
    order_status     varchar(10)    NOT NULL COMMENT '订单状态',
    PRIMARY KEY (id),
    UNIQUE KEY uk_order_no (order_no)
) COMMENT = '订单信息表';
