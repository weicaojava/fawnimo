/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Authorize exact original-project media and an explicit reference purpose, without accepting URLs or paths.
 */
export type AssetImageReferenceInput = {
    media_id: string;
    expected_sha256: string;
    purpose: 'appearance' | 'composition' | 'style';
};

