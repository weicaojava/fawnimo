/* generated using openapi-typescript-codegen -- do not edit */
/* istanbul ignore file */
/* tslint:disable */
/* eslint-disable */
import type { TrialRegistrationRead } from './TrialRegistrationRead';
/**
 * Distinguish the supported legacy local studio from a login-required gateway.
 */
export type IdentityStatus = {
    mode: 'local' | 'workspace';
    registration_enabled: boolean;
    trial_registration?: (TrialRegistrationRead | null);
};

