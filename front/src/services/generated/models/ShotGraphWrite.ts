/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Compare the saved complete graph and its revision before applying one idempotent command.
 */
export type ShotGraphWrite = {
    project_id: string;
    chapter_id: string;
    request_key: string;
    expected_revision: number;
    expected_fingerprint: string;
    action: 'reorder' | 'insert' | 'merge';
    ordered_shot_ids?: Array<string>;
    source_shot_ids?: Array<string>;
    after_shot_id?: (string | null);
    title?: (string | null);
};

