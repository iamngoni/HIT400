from api.views.auth.serializers.model import UserModelSerializer
from api.views.auth.serializers.payload import (
    SignInPayloadSerializer,
    RefreshTokenSerializer,
    ForgotPasswordPayloadSerializer,
    ResetPasswordPayloadSerializer,
)
from rest_framework.parsers import JSONParser
from rest_framework.renderers import JSONRenderer
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from loguru import logger
import jwt
from decouple import config
from django.utils import timezone

from auth0.models import BlacklistToken
from services.exceptions.passwords import PasswordUsedException
from services.helpers.api_response import api_response
from services.helpers.get_client_details import get_client_details
from services.jwt_service import generate_jwt_payload
from api.views.auth.tasks import (
    notify_user_about_login_activity,
    send_verification_code_to_user,
    send_password_reset_otp,
)
from users.models import User
from rest_framework.permissions import IsAuthenticated


class SignInView(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    serializer_class = SignInPayloadSerializer
    authentication_classes = ()

    def post(self, request):
        try:
            payload = self.serializer_class(data=request.data)

            if payload.is_valid():
                username = payload.data.get("username")
                password = payload.data.get("password")

                user = authenticate(username=username, password=password)

                logger.info("USER: ", user)

                if user is None:
                    return api_response(
                        request=request,
                        num_status=401,
                        bool_status=False,
                        message="Incorrect username/email or password",
                    )

                if user is not None:
                    jwt_payload = generate_jwt_payload(user)

                    access_token = jwt.encode(
                        payload=jwt_payload["access"],
                        key=config("JWT_SECRET"),
                        algorithm="HS256",
                    )
                    refresh_token = jwt.encode(
                        payload=jwt_payload["refresh"],
                        key=config("JWT_SECRET"),
                        algorithm="HS256",
                    )

                    # notify user of login activity
                    details = get_client_details(request)
                    notify_user_about_login_activity.delay(user, details)

                    # if user hasn't been verified send another otp code
                    if not user.is_verified:
                        send_verification_code_to_user.delay(request.user)

                    return api_response(
                        request=request,
                        data={
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                            "user": UserModelSerializer(user).data,
                        },
                    )

                else:
                    return api_response(
                        request=request,
                        num_status=401,
                        bool_status=False,
                        message="No user found",
                    )
            else:
                return api_response(
                    request=request,
                    num_status=400,
                    bool_status=False,
                    issues=payload.errors,
                )
        except Exception as e:
            logger.error(f" {e}")
            return api_response(request=request, num_status=500, bool_status=False)


class RefreshAuthView(APIView):
    renderer_classes = (JSONRenderer,)
    parser_classes = (JSONParser,)
    authentication_classes = ()
    serializer_class = RefreshTokenSerializer

    def post(self, request):
        payload = self.serializer_class(data=request.data)

        if payload.is_valid():
            refresh_token = payload.data.get("refresh_token")

            try:
                decoded_refresh_token = jwt.decode(
                    jwt=refresh_token,
                    key=config("JWT_SECRET"),
                    options={"require": ["exp", "iat", "iss"]},
                    algorithms=["HS256"],
                )

                if decoded_refresh_token.get("type") != "refresh":
                    return api_response(
                        request=request,
                        num_status=401,
                        bool_status=False,
                        message="Incorrect token type",
                    )

                user_id = decoded_refresh_token.get("id")
                user = User.get_item_by_id(pk=user_id)

                if user is not None:

                    # destroy old token
                    authorization_header = request.headers.get("Authorization")
                    token = authorization_header.split(" ")[1]

                    blacklist = BlacklistToken(token=token)
                    blacklist.save()

                    jwt_payload = generate_jwt_payload(user=user)
                    access_token = jwt.encode(
                        payload=jwt_payload["access"],
                        key=config("JWT_SECRET"),
                        algorithm="HS256",
                    )
                    refresh_token = jwt.encode(
                        payload=jwt_payload["refresh"],
                        key=config("JWT_SECRET"),
                        algorithm="HS256",
                    )

                    return api_response(
                        request=request,
                        data={
                            "access_token": access_token,
                            "refresh_token": refresh_token,
                            "user": UserModelSerializer(user).data,
                        },
                    )
                else:
                    return api_response(
                        request=request,
                        num_status=404,
                        bool_status=False,
                        message="No user associated with token",
                    )

            except Exception as e:
                logger.error(f" {e}")
                return api_response(request=request, num_status=500, bool_status=False)

        else:
            return api_response(
                request=request,
                num_status=400,
                bool_status=False,
                issues=payload.errors,
            )


class DestroyTokenView(APIView):
    renderer_classes = [JSONRenderer]
    parser_classes = [JSONParser]
    permission_classes = (IsAuthenticated,)

    def get(self, request):
        try:
            authorization_header = request.headers.get("Authorization")
            token = authorization_header.split(" ")[1]

            blacklist = BlacklistToken(token=token)
            blacklist.save()

            return api_response(request=request)
        except Exception as e:
            logger.error(f" Failed to destroy token: {e}")
            return api_response(request=request, num_status=500, bool_status=False)


class ForgotPasswordView(APIView):
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)
    authentication_classes = ()
    serializer_class = ForgotPasswordPayloadSerializer

    def post(self, request):
        try:
            payload = self.serializer_class(data=request.data)
            if payload.is_valid():
                user = User.objects.get(email=payload.data.get("email"))
                user.generate_one_time_pin()
                user.save()

                send_password_reset_otp.delay(user)
                return api_response(
                    request=request,
                )
            else:
                logger.error(f" {payload.errors}")
                return api_response(
                    request=request,
                    num_status=400,
                    bool_status=False,
                    issues=payload.errors,
                )
        except User.DoesNotExist:
            logger.error(" User does not exist")
            return api_response(
                request=request,
            )
        except Exception as e:
            logger.error(f" {e}")
            return api_response(request=request, num_status=500, bool_status=False)


class ResetPasswordView(APIView):
    parser_classes = (JSONParser,)
    renderer_classes = (JSONRenderer,)
    authentication_classes = ()
    serializer_class = ResetPasswordPayloadSerializer

    def post(self, request):
        try:
            payload = self.serializer_class(data=request.data)
            if payload.is_valid():
                otp = payload.data.get("otp")
                user = User.objects.get(one_time_pin=otp)

                issued_at_time = user.one_time_pin_generated_at
                current_time = timezone.now()
                time_difference = current_time - issued_at_time
                logger.info(
                    f" time difference between issued at and now is {time_difference.total_seconds()} in seconds"
                )
                time_difference = divmod(time_difference.total_seconds(), 60)
                difference_in_minutes = time_difference[0]
                logger.info(
                    f" time difference between issued at and now is {difference_in_minutes} in minutes"
                )

                if difference_in_minutes > 10:
                    return api_response(
                        request=request,
                        num_status=400,
                        bool_status=False,
                        message="OTP Expired",
                    )

                # clear the one time pins
                user.one_time_pin = None
                user.one_time_pin_generated_at = None
                user.set_password(payload.data.get("password"))
                user.save()

                jwt_payload = generate_jwt_payload(user)

                access_token = jwt.encode(
                    payload=jwt_payload["access"],
                    key=config("JWT_SECRET"),
                    algorithm="HS256",
                )
                refresh_token = jwt.encode(
                    payload=jwt_payload["refresh"],
                    key=config("JWT_SECRET"),
                    algorithm="HS256",
                )

                # notify user of login activity
                details = get_client_details(request)
                notify_user_about_login_activity.delay(user, details)

                return api_response(
                    request=request,
                    data={
                        "access_token": access_token,
                        "refresh_token": refresh_token,
                        "user": UserModelSerializer(user).data,
                    },
                )
            else:
                logger.error(f" {payload.errors}")
                return api_response(
                    request=request,
                    num_status=400,
                    bool_status=False,
                    issues=payload.errors,
                )
        except User.DoesNotExist:
            logger.error(" User does not exist")
            return api_response(request=request, num_status=404, bool_status=False)
        except PasswordUsedException as e:
            logger.error("password has been used before")
            return api_response(
                request=request, num_status=400, bool_status=False, message=str(e)
            )
        except Exception as e:
            logger.error(f" {e}")
            return api_response(request=request, num_status=500, bool_status=False)
