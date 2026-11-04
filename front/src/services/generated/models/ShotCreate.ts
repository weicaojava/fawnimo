/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ShotStatus } from './ShotStatus';
/**
 * Keep legacy two-step clients while allowing the manual editor to create a usable shot atomically.
 */
export type ShotCreate = {
    /**
     * 镜头 ID
     */
    id: string;
    /**
     * 所属章节 ID
     */
    chapter_id: string;
    /**
     * 镜头序号（章节内唯一）
     */
    index: number;
    /**
     * 镜头标题
     */
    title: string;
    /**
     * 缩略图 URL/路径
     */
    thumbnail?: string;
    /**
     * 镜头状态
     */
    status?: ShotStatus;
    /**
     * 是否明确跳过信息提取
     */
    skip_extraction?: boolean;
    /**
     * 剧本摘录
     */
    script_excerpt?: string;
    /**
     * 已生成视频关联的文件 ID（files.id，type=video）
     */
    generated_video_file_id?: (string | null);
    /**
     * 在同一事务中建立空白镜头详情；旧客户端可继续单独创建详情
     */
    create_detail?: boolean;
};

