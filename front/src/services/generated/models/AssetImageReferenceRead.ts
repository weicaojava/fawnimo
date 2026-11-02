/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Expose actual input proof without private storage paths or data URLs.
 */
export type AssetImageReferenceRead = {
    media_id: string;
    position: number;
    purpose: 'appearance' | 'composition' | 'style';
    sha256: string;
    byte_size: number;
    width: number;
    height: number;
    mime: string;
};

