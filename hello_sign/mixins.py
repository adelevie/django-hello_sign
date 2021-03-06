# -*- coding: utf-8 -*-
from django.conf import settings
from django.contrib.contenttypes.models import ContentType

from jsonfield import JSONField

from .services import HelloSignService, HelloSignSignerService
from .models import HelloSignRequest

from . import logger

import json
import datetime

HELLOSIGN_TEST_MODE = getattr(settings, 'HELLOSIGN_TEST_MODE', 1)


class OverrideModelMethodException(Exception):
    message = 'You must override this method'


class ModelContentTypeMixin(object):
    """
    Mixin to provide get_content_type_object of the model its mixed in with
    """
    def get_content_type_object(self):
        """
        Required Method for acessing the current models ContentType
        """
        return ContentType.objects.get(app_label=self._meta.app_label,
                                       model=self._meta.model_name)


class HelloSignModelMixin(ModelContentTypeMixin):
    data = JSONField(default={})  # required to store data

    def hellosign_requests(self):
        """
        QuerySet of HelloSignRequest objects
        relies on HelloSignRequest._meta to define order
        """
        return HelloSignRequest.objects.filter(object_id=self.pk,
                                               content_object_type=self.get_content_type_object())

    @property
    def hellosign(self):
        """
        The Latest HelloSign Request
        """
        return self.hellosign_requests().first()

    @property
    def signing_data(self):
        return getattr(self.hellosign, 'data', None)

    @property
    def signature_request_id(self):
        return getattr(self.hellosign, 'signature_request_id', None)

    def sign_url_for_email(self, signer_email):
        """
        Get the sign_url for a specific email address
        """
        service = HelloSignSignerService(obj=self, signer_email=signer_email)
        return service.sign_url_for_signer(email=signer_email)

    @property
    def signatures(self):
        return self.signing_data.get('signatures', [])

    @signatures.setter
    def signatures(self, value):
        """
        Setter to allow us to update the signatures details segment of the
        current json object
        """
        obj = self.hellosign  # most recent HSRequest object
        obj.data['signatures'] = value
        obj.save(update_fields=['data'])

        # and the local model
        self.data['signatures'] = value
        self.save(update_fields=['data'])

    def hs_subject(self):
        """
        Simple subject sent as part of the HelloSign request used by HS
        in communication and in their own web interface
        """
        return 'Signature Request for %s' % self

    def hs_message(self):
        """
        Descriptive message sent as part of the HelloSign request used by HS
        in communication and in their own web interface
        """
        return 'Please review and sign this document at your earliest convenience'

    def hs_signers(self):
        """
        Return a list of invitees to sign
        Order here is important as it ties in tightly with the HS signers
        unfortunately
        """
        return [{'name': u.get_full_name(), 'email': u.email} for u in [self.workspace.lawyer, self.user]]

    def hs_document_title(self):
        """
        Method to set the document title, displayed in the HelloSign Interface
        """
        raise OverrideModelMethodException('You must override hs_document_title and return the title of the document to send to HelloSign')

    def hs_document(self):
        """
        Return the document to be senf for signing
        """
        raise OverrideModelMethodException('You must override hs_document and return the pdf/docx/doc file to send to HelloSign')

    def get_hs_service(self):
        """
        Return the HelloSign service instance with all required to send for
        signing
        """
        return HelloSignService(document=self.hs_document(),
                                title=self.hs_document_title(),
                                invitees=self.hs_signers(),
                                subject=self.hs_subject(),
                                message=self.hs_message())

    def send_for_signing(self):
        """
        Primary method used to send a document for signing
        """
        service = self.get_hs_service()
        resp = service.send_for_signing(test_mode=HELLOSIGN_TEST_MODE,
                                        client_id=settings.HELLOSIGN_CLIENT_ID)
        # resp = service.send_for_signing(test_mode=HELLOSIGN_TEST_MODE)

        if 'signature_request' not in resp.json() or resp.status_code not in [200]:
            raise Exception('Could not send document for signing at HelloSign: %s' % resp.json())

        result = resp.json()['signature_request']
        #
        # Add the date because HelloSign does not have a date
        #
        result.update({
            'date_sent': str(datetime.datetime.utcnow())
        })

        # setup the hs request object
        signature_request_id = result.get('signature_request_id') # get id
        hellosign_request = HelloSignRequest.objects.create(signature_request_id=signature_request_id,
                                                            content_object_type=self.get_content_type_object(),
                                                            object_id=self.pk,
                                                            data=result)
        #
        # Update with our set date_sent variable
        #
        resp._content = json.dumps(result)
        logger.info('Got HelloSign Signature Response: %s' % resp)

        return resp
