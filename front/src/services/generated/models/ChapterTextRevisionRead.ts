/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * One actual saved text revision and its restore provenance.
 */
export type ChapterTextRevisionRead = {
    id: string;
    chapter_id: string;
    revision: number;
    raw_text: string;
    condensed_text: string;
    content_hash: string;
    origin: string;
    restored_from_revision: (number | null);
    created_at: string;
};

