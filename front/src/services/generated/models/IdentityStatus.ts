/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
/**
 * Distinguish the supported legacy local studio from a login-required gateway.
 */
export type IdentityStatus = {
    mode: 'local' | 'workspace';
    registration_enabled: boolean;
};

