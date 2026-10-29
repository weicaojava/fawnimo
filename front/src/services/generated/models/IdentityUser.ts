/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Safe account fields; password hashes and session tokens are never serialized.
 */
export type IdentityUser = {
    id: string;
    account: string;
    display_name: string;
    language: 'zh-CN' | 'en-US';
    theme: 'light' | 'dark';
};

