/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Safe publication view; payload omits private storage credentials.
 */
export type EditPublicationRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    draft_id: string;
    draft_revision: number;
    content_hash: string;
    payload: Record<string, any>;
};

