/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * A caller can choose an owned media identity and hash, never a URL, path, account or credential.
 */
export type VideoReferenceInput = {
    media_id: string;
    expected_sha256: string;
    role: 'first_frame' | 'last_frame' | 'reference_image' | 'reference_video';
};

