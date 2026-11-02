/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Portable display view; its bytes are distinct from the private evidence hash.
 */
export type SoundPublicationRead = {
    id: string;
    project_id: string;
    chapter_id: string;
    draft_revision: number;
    content_hash: string;
    payload: Record<string, any>;
    model_calls?: number;
};

