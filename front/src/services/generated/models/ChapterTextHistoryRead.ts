/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { ChapterTextRevisionRead } from './ChapterTextRevisionRead';
/**
 * A real paged history; legacy chapters honestly start with no recorded revisions.
 */
export type ChapterTextHistoryRead = {
    chapter_id: string;
    current_revision: number;
    raw_text: string;
    condensed_text: string;
    items: Array<ChapterTextRevisionRead>;
    page: number;
    page_size: number;
    total: number;
};

