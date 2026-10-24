/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Create an asset attached to the same existing project's legacy library.
 */
export type AssetWrite = {
    description?: string;
    media_ids?: Array<string>;
    expected_revision?: number;
    kind: 'character' | 'appearance' | 'scene' | 'prop' | 'voice';
    name: string;
    subject_id?: (string | null);
};

